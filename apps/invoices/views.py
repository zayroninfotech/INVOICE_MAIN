from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.http import FileResponse, HttpResponse
from django.conf import settings
from apps.authentication.authentication import MongoJWTAuthentication
from apps.authentication.permissions import IsReadOnlyForUser, IsAdminOrSuperAdmin, IsSuperAdmin
from apps.authentication.models import BusinessProfile
from apps.subscriptions.entitlements import can_create_invoice, increment_usage, get_plan_for_request
from .models import Invoice
from .serializers import InvoiceSerializer, InvoiceListSerializer, InvoiceDetailSerializer
from .pdf_generator import generate_invoice_pdf
from . import template_registry, report_export
from .emailer import send_invoice_email, approval_url, MailNotConfigured, explain_send_error
from utils.response import success, error
from datetime import datetime
import logging
import os
import re

logger = logging.getLogger(__name__)


def _limit_message(plan, reason):
    limits = settings.PLAN_LIMITS.get(plan, {})
    label = limits.get('label', plan.capitalize())
    if reason == 'daily_limit':
        d = limits.get('invoices_per_day', '?')
        return f"Daily limit of {d} invoices reached on your {label} plan. Upgrade to create more."
    if reason == 'monthly_limit':
        m = limits.get('invoices_per_month', '?')
        return f"Monthly limit of {m} invoices reached on your {label} plan. Upgrade to create more."
    return "Invoice limit reached. Upgrade to create more."


def _user_plan(request):
    if getattr(request.user, 'role', '') == 'superadmin':
        return 'unlimited'
    plan, _ = get_plan_for_request(request)
    return plan


def _purchased_templates(request):
    """Template ids this user bought individually — empty for anonymous users."""
    _, sub = get_plan_for_request(request)
    return list(sub.purchased_templates) if sub else []


def _check_template_allowed(request, template_id):
    """Returns None if allowed, else an error Response."""
    plan = _user_plan(request)
    tdef = template_registry.get_template_def(template_id)
    if not tdef:
        return error(f"Unknown template '{template_id}'.", status=400)
    min_plan = template_registry.effective_min_plan(template_id)
    if template_registry.is_blocked(template_id):
        return error(
            f"The '{tdef['name']}' template is currently unavailable.",
            {"upgrade_required": False, "blocked": True}, status=403)
    from .template_registry import PLAN_RANK
    if template_id in _purchased_templates(request):
        return None
    if PLAN_RANK.get(plan, 0) < PLAN_RANK.get(min_plan, 0):
        need_label = settings.PLAN_LIMITS.get(
            'pro' if min_plan == 'unlimited' else min_plan, {}).get('label', min_plan.capitalize())
        return error(
            f"The '{tdef['name']}' template requires the {need_label} plan or higher. Upgrade to use it.",
            {"upgrade_required": True, "current_plan": plan,
             "required_plan": min_plan, "template_id": template_id},
            status=403)
    return None


def _invoice_qs(user):
    if getattr(user, 'role', '') == 'superadmin':
        return Invoice.objects()
    return Invoice.objects(created_by=str(user.pk))


def _filtered_invoice_qs(request):
    qp = request.query_params
    queryset = _invoice_qs(request.user)
    if qp.get('status'):
        queryset = queryset.filter(status=qp['status'])
    payment_filter = qp.get('payment', '')
    if payment_filter == 'Due':
        queryset = queryset.filter(status__nin=['Paid', 'Cancelled'])
    elif payment_filter in ('Paid', 'Cancelled'):
        queryset = queryset.filter(status=payment_filter)
    approval_filter = qp.get('approval', '')
    if approval_filter in ('In Process', 'Approved', 'Disapproved'):
        queryset = queryset.filter(approval_status=approval_filter)
    search = qp.get('search', '').strip()
    if search:
        pattern = re.escape(search)
        queryset = queryset.filter(__raw__={'$or': [
            {'invoice_number': {'$regex': pattern, '$options': 'i'}},
            {'customer_name': {'$regex': pattern, '$options': 'i'}},
        ]})
    return queryset


class InvoiceExportView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        fmt = request.query_params.get('format', 'pdf')
        if fmt not in ('pdf', 'csv'):
            return error("Format must be pdf or csv.")
        invoices = list(_filtered_invoice_qs(request).order_by('-invoice_date'))
        stamp = datetime.now().strftime('%Y%m%d-%H%M')
        if fmt == 'csv':
            resp = HttpResponse(report_export.build_csv(invoices), content_type='text/csv; charset=utf-8')
            resp['Content-Disposition'] = f'attachment; filename="invoices-{stamp}.csv"'
            return resp

        qp = request.query_params
        parts = [f"Payment: {qp['payment']}" if qp.get('payment') else '',
                 f"Approval: {qp['approval']}" if qp.get('approval') else '',
                 f"Search: \"{qp['search'].strip()[:40]}\"" if qp.get('search', '').strip() else '']
        filters_text = ' · '.join(p for p in parts if p) or 'All invoices'
        bp = BusinessProfile.objects(user_id=str(request.user.pk)).first()
        pdf = report_export.build_pdf(invoices, company=(bp.company_name if bp else ''),
                                      filters_text=filters_text,
                                      generated_by=getattr(request.user, 'username', ''))
        resp = HttpResponse(pdf, content_type='application/pdf')
        resp['Content-Disposition'] = f'attachment; filename="invoice-report-{stamp}.pdf"'
        return resp


class InvoiceSummaryView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        qs = _invoice_qs(request.user)
        all_list = list(qs.only('grand_total', 'status', 'invoice_date', 'customer_name'))
        total_count = len(all_list)
        total_amount = sum(float(inv.grand_total or 0) for inv in all_list)
        due_list = [inv for inv in all_list if inv.status not in ('Paid', 'Cancelled')]
        due_count = len(due_list)
        due_amount = sum(float(inv.grand_total or 0) for inv in due_list)
        paid_list = [inv for inv in all_list if inv.status == 'Paid']
        paid_count = len(paid_list)
        paid_amount = sum(float(inv.grand_total or 0) for inv in paid_list)
        cancelled_count = sum(1 for inv in all_list if inv.status == 'Cancelled')

        now = datetime.utcnow()
        months = []
        for i in range(5, -1, -1):
            y, m = now.year, now.month - i
            while m <= 0:
                m += 12
                y -= 1
            months.append((y, m))
        buckets = {k: {'billed': 0.0, 'paid': 0.0} for k in months}
        customers = {}
        for inv in all_list:
            if inv.status == 'Cancelled':
                continue
            amt = float(inv.grand_total or 0)
            d = inv.invoice_date
            if d and (d.year, d.month) in buckets:
                buckets[(d.year, d.month)]['billed'] += amt
                if inv.status == 'Paid':
                    buckets[(d.year, d.month)]['paid'] += amt
            name = (inv.customer_name or '').strip() or '—'
            c = customers.setdefault(name, {'name': name, 'amount': 0.0, 'count': 0})
            c['amount'] += amt
            c['count'] += 1
        monthly = [{'label': datetime(y, m, 1).strftime('%b'),
                    'billed': round(buckets[(y, m)]['billed'], 2),
                    'paid': round(buckets[(y, m)]['paid'], 2)} for y, m in months]
        top_customers = sorted(customers.values(), key=lambda c: -c['amount'])[:5]
        for c in top_customers:
            c['amount'] = round(c['amount'], 2)

        return success({
            'monthly': monthly,
            'top_customers': top_customers,
            'total_count': total_count,
            'total_amount': round(total_amount, 2),
            'due_count': due_count,
            'due_amount': round(due_amount, 2),
            'paid_count': paid_count,
            'paid_amount': round(paid_amount, 2),
            'cancelled_count': cancelled_count,
        })


class InvoiceListCreateView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        queryset = _filtered_invoice_qs(request)
        page = int(request.query_params.get('page', 1))
        page_size = int(request.query_params.get('page_size', 20))
        offset = (page - 1) * page_size
        total = queryset.count()
        invoices = queryset.skip(offset).limit(page_size)
        return success({
            'total': total,
            'page': page,
            'page_size': page_size,
            'results': InvoiceListSerializer(invoices, many=True).data,
        })

    def post(self, request):
        ok, reason, plan, sub = can_create_invoice(request)
        if not ok:
            return error(
                _limit_message(plan, reason),
                {"upgrade_required": True, "current_plan": plan, "reason": reason},
                status=402,
            )

        tpl_err = _check_template_allowed(request, request.data.get('template_style', 'classic'))
        if tpl_err is not None:
            return tpl_err

        serializer = InvoiceSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        invoice = serializer.save()

        increment_usage(request, plan, sub)

        try:
            from apps.authentication.models import AuditLog
            AuditLog.log(request.user, 'invoice_created',
                         f'{invoice.invoice_number} — {invoice.customer_name} — ₹{invoice.grand_total}')
        except Exception:
            pass

        return success(InvoiceDetailSerializer(invoice).data, "Invoice created.", 201)


class InvoiceDetailView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def _get_invoice(self, pk, user):
        return _invoice_qs(user).filter(pk=pk).first()

    def get(self, request, pk):
        invoice = self._get_invoice(pk, request.user)
        if not invoice:
            return error("Invoice not found.", status=404)
        return success(InvoiceDetailSerializer(invoice).data)

    def put(self, request, pk):
        invoice = self._get_invoice(pk, request.user)
        if not invoice:
            return error("Invoice not found.", status=404)
        if invoice.status in ['Paid', 'Cancelled']:
            return error(f"Cannot edit a {invoice.status} invoice.")
        tpl_err = _check_template_allowed(request, request.data.get('template_style', invoice.template_style))
        if tpl_err is not None:
            return tpl_err
        serializer = InvoiceSerializer(invoice, data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        # invoice_number is read-only on the serializer (it is minted on create),
        # but the detail page lets the owner renumber in place. It becomes the
        # PDF filename, so only filename-safe characters are accepted.
        new_no = str(request.data.get('invoice_number') or '').strip()
        if new_no and new_no != invoice.invoice_number:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,39}', new_no):
                return error("Invoice number can use letters, numbers, '-', '_' and '.' only (max 40).",
                             {"invoice_number": ["Invalid characters."]})
            if Invoice.objects(created_by=invoice.created_by, invoice_number=new_no, pk__ne=invoice.pk).first():
                return error(f"Invoice number {new_no} is already used by another invoice.",
                             {"invoice_number": ["Already in use."]})
            invoice.invoice_number = new_no
        invoice = serializer.update(invoice, serializer.validated_data)
        return success(InvoiceDetailSerializer(invoice).data, "Invoice updated.")

    def delete(self, request, pk):
        invoice = self._get_invoice(pk, request.user)
        if not invoice:
            return error("Invoice not found.", status=404)
        if invoice.status == 'Paid':
            return error("Cannot delete a paid invoice.")
        invoice.status = 'Cancelled'
        invoice.save()
        return success(message="Invoice cancelled.")


class InvoicePDFView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request, pk):
        invoice = _invoice_qs(request.user).filter(pk=pk).first()
        if not invoice:
            return error("Invoice not found.", status=404)
        bp = BusinessProfile.objects(user_id=invoice.created_by).first()
        pdf_path = generate_invoice_pdf(invoice, business_profile=bp)
        invoice.pdf_path = pdf_path
        invoice.save()
        full_path = os.path.join(settings.MEDIA_ROOT, pdf_path)
        if not os.path.exists(full_path):
            return error("PDF generation failed.", status=500)
        f = open(full_path, 'rb')
        response = FileResponse(f, content_type='application/pdf',
                                as_attachment=True,
                                filename=f"{invoice.invoice_number}.pdf")
        response['X-Accel-Buffering'] = 'no'
        return response


class InvoiceEmailView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAdminOrSuperAdmin]

    def post(self, request, pk):
        invoice = _invoice_qs(request.user).filter(pk=pk).first()
        if not invoice:
            return error("Invoice not found.", status=404)

        to = (request.data.get('to') or invoice.customer_email or '').strip()
        if not to:
            return error("No recipient email address.")
        note = (request.data.get('message') or '').strip()[:2000]

        bp = BusinessProfile.objects(user_id=invoice.created_by).first()
        pdf_path = generate_invoice_pdf(invoice, business_profile=bp)
        invoice.pdf_path = pdf_path
        # Minted here rather than at creation: an invoice that was never sent
        # should have no live public URL to guess at.
        invoice.ensure_share_token()
        invoice.save()

        base_url = getattr(settings, 'SITE_URL', '') or request.build_absolute_uri('/')
        try:
            send_invoice_email(
                invoice,
                seller_name=(bp.company_name if bp else '') or invoice.signature_company,
                reply_to=(bp.email if bp else '') or '',
                to=[to], note=note, base_url=base_url,
                pdf_abs_path=os.path.join(settings.MEDIA_ROOT, pdf_path),
            )
        except MailNotConfigured as exc:
            return error(str(exc), status=503)
        except Exception as exc:
            logger.warning("invoice email failed for %s", invoice.pk, exc_info=True)
            return error(explain_send_error(exc), status=502)

        # Only 'Sent' on the way out of Draft — never demote a Paid invoice.
        if invoice.status == 'Draft':
            invoice.status = 'Sent'
        invoice.sent_at = datetime.utcnow()
        invoice.save()
        return success({'to': to, 'approval_url': approval_url(invoice, base_url)},
                       f"Invoice sent to {to}.")


class PublicInvoiceView(APIView):
    """The customer's side of an emailed invoice — no account, no login.

    Authorisation is the share_token itself, so it is minted with
    secrets.token_urlsafe(32) and only ever handed out in the email.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        invoice = Invoice.objects(share_token=token).first() if token else None
        if not invoice:
            return error("This invoice link is not valid.", status=404)
        return success({
            'invoice_number':  invoice.invoice_number,
            'approval_status': invoice.approval_status,
            'approval_note':   invoice.approval_note,
            'payment_status':  invoice.payment_status,
        })

    def post(self, request, token):
        invoice = Invoice.objects(share_token=token).first() if token else None
        if not invoice:
            return error("This invoice link is not valid.", status=404)

        action = (request.data.get('action') or '').strip().lower()
        if action not in ('approve', 'disapprove'):
            return error("Choose either approve or disapprove.")

        note = (request.data.get('note') or '').strip()[:2000]
        if action == 'disapprove' and not note:
            return error("Please say why you are declining this invoice.")

        invoice.approval_status = 'Approved' if action == 'approve' else 'Disapproved'
        invoice.approval_note = note
        invoice.approval_by = (request.data.get('name') or '').strip()[:120] \
            or invoice.customer_recipient or invoice.customer_name
        invoice.approval_at = datetime.utcnow()
        invoice.save()
        return success({'approval_status': invoice.approval_status},
                       "Thank you — your response has been recorded.")


class PublicInvoicePDFView(APIView):
    """PDF download from the customer's approval page, authorised by the token."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request, token):
        invoice = Invoice.objects(share_token=token).first() if token else None
        if not invoice:
            return error("This invoice link is not valid.", status=404)
        bp = BusinessProfile.objects(user_id=invoice.created_by).first()
        pdf_path = generate_invoice_pdf(invoice, business_profile=bp)
        full_path = os.path.join(settings.MEDIA_ROOT, pdf_path)
        if not os.path.exists(full_path):
            return error("PDF generation failed.", status=500)
        return FileResponse(open(full_path, 'rb'), content_type='application/pdf',
                            as_attachment=True,
                            filename=f"{invoice.invoice_number}.pdf")


class InvoiceStatusView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAdminOrSuperAdmin]

    def patch(self, request, pk):
        invoice = _invoice_qs(request.user).filter(pk=pk).first()
        if not invoice:
            return error("Invoice not found.", status=404)
        new_status = request.data.get('status')
        valid = ['Draft', 'Sent', 'Paid', 'Partial', 'Overdue', 'Cancelled']
        if new_status not in valid:
            return error(f"Invalid status. Choose from: {', '.join(valid)}")
        invoice.status = new_status
        invoice.save()
        return success({'status': invoice.status}, "Status updated.")


class AvailableTemplatesView(APIView):
    """Template catalogue for the invoice form — includes per-user plan unlock state."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        plan = _user_plan(request)
        from .template_registry import PLAN_RANK, SECTION_LABELS
        user_rank = PLAN_RANK.get(plan, 0)
        owned = _purchased_templates(request)
        templates = []
        for t in template_registry.templates_payload():
            min_plan = t['effective_min_plan']
            by_plan = user_rank >= PLAN_RANK.get(min_plan, 0)
            is_owned = t['id'] in owned
            templates.append({
                'id': t['id'],
                'name': t['name'],
                'desc': t['desc'],
                'min_plan': min_plan,
                'badge': t.get('badge', '#F8FAFC'),
                'blocked': t['blocked'],
                'owned': is_owned,
                'unlocked': (by_plan or is_owned) and not t['blocked'],
            })
        return success({
            'plan': plan,
            'templates': templates,
            'sections': SECTION_LABELS,
            'default_section_order': template_registry.SECTIONS,
        })


class TemplatePurchaseView(APIView):
    """Unlock one premium template for the current subscription period."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def post(self, request, template_id):
        tdef = template_registry.get_template_def(template_id)
        if not tdef:
            return error(f"Unknown template '{template_id}'.", status=400)
        if template_registry.is_blocked(template_id):
            return error(f"The '{tdef['name']}' template is currently unavailable.", status=403)

        _, sub = get_plan_for_request(request)
        if sub is None:
            return error("Sign in to buy a template.", status=403)
        if template_id not in sub.purchased_templates:
            sub.purchased_templates.append(template_id)
            sub.save()
        return success(
            {'template_id': template_id, 'name': tdef['name']},
            f"'{tdef['name']}' is yours — it's now in your Invoice tab.")


# ── Custom templates (superadmin "Add Invoice") ─────────────────────────────

def _custom_template_data(c):
    from .template_registry import TEMPLATE_MAP, effective_min_plan, is_blocked
    base = TEMPLATE_MAP.get(c.base, {})
    return {
        'id': c.template_id, 'name': c.name, 'desc': c.desc, 'base': c.base,
        'base_name': base.get('name', c.base), 'accent': c.accent,
        'min_plan': c.min_plan, 'blocked': is_blocked(c.template_id),
        'status': getattr(c, 'status', None) or 'published',
        'mode': getattr(c, 'mode', 'base') or 'base',
        'ai_status': getattr(c, 'ai_status', '') or '',
        'page_style': getattr(c, 'page_style', 'light') or 'light',
        'ai_error': getattr(c, 'ai_error', '') or '',
        'mapped': sum(1 for sp in (getattr(c, 'layout', None) or {}).get('spans', [])
                      if sp.get('field') and sp.get('field') != 'hide'),
        'source_name': getattr(c, 'source_name', '') or '',
        'preview_url': (settings.MEDIA_URL + c.preview_path) if getattr(c, 'preview_path', '') else '',
        'created_at': c.created_at.isoformat() if c.created_at else None,
    }


def _custom_template_fields(data, partial=False):
    """Validate the editable fields; returns (fields, error_message)."""
    from .template_registry import TEMPLATE_MAP
    out = {}
    if 'name' in data or not partial:
        name = str(data.get('name') or '').strip()
        if not name:
            return None, "Give the template a name."
        if len(name) > 60:
            return None, "Name can be at most 60 characters."
        out['name'] = name
    if 'desc' in data:
        out['desc'] = str(data.get('desc') or '').strip()[:200]
    if 'base' in data or not partial:
        base = str(data.get('base') or '').strip()
        if base not in TEMPLATE_MAP:
            return None, "Choose a base layout."
        out['base'] = base
    if 'accent' in data:
        accent = str(data.get('accent') or '').strip()
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', accent):
            return None, "Colour must be a hex value like #C1121F."
        out['accent'] = accent.upper()
    if 'min_plan' in data:
        plan = str(data.get('min_plan') or '').strip()
        if plan not in ('free', 'plus', 'pro', 'unlimited'):
            return None, "Plan must be free, plus, pro or unlimited."
        out['min_plan'] = plan
    if 'page_style' in data:
        ps = str(data.get('page_style') or 'light')
        if ps not in ('light', 'dark'):
            return None, "Page colour must be light or dark."
        out['page_style'] = ps
    return out, None


class CustomTemplateListView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        from .models import CustomTemplate
        from .template_registry import INVOICE_TEMPLATES
        return success({
            'templates': [_custom_template_data(c) for c in CustomTemplate.objects(is_active=True)],
            'bases': [{'id': t['id'], 'name': t['name'], 'desc': t['desc']} for t in INVOICE_TEMPLATES],
        })

    def post(self, request):
        import secrets
        from .models import CustomTemplate
        fields, err = _custom_template_fields(request.data)
        if err:
            return error(err)
        fields.setdefault('accent', '#C1121F')
        fields.setdefault('min_plan', 'plus')
        c = CustomTemplate(template_id='c-' + secrets.token_hex(4), status='draft',
                           created_by=str(request.user.pk), **fields).save()
        try:
            from apps.authentication.models import AuditLog
            AuditLog.log(request.user, 'template_added', f'{c.name} ({c.template_id}) on {c.base}')
        except Exception:
            pass
        return success(_custom_template_data(c), f"“{c.name}” saved as a draft. Click Apply to put it in Buy Invoice.", 201)


class CustomTemplateDetailView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def _get(self, pk):
        from .models import CustomTemplate
        return CustomTemplate.objects(template_id=pk, is_active=True).first()

    def put(self, request, pk):
        c = self._get(pk)
        if not c:
            return error("Template not found.", status=404)
        fields, err = _custom_template_fields(request.data, partial=True)
        if err:
            return error(err)
        for k, v in fields.items():
            setattr(c, k, v)
        c.save()
        if 'min_plan' in fields:
            # A /z-admin/ plan override would otherwise win over the new value.
            from apps.authentication.models import TemplateConfig
            TemplateConfig.objects(template_id=c.template_id).delete()
        return success(_custom_template_data(c), "Template updated.")

    def delete(self, request, pk):
        c = self._get(pk)
        if not c:
            return error("Template not found.", status=404)
        # Soft delete: invoices already using it fall back to its base layout
        # via resolve_template() instead of breaking.
        c.is_active = False
        c.save()
        return success(message=f"“{c.name}” removed from Buy Invoice.")


class CustomTemplateFromDocView(APIView):
    """Upload an invoice design (PDF/image) → a draft template built from it."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        import secrets
        from .models import CustomTemplate
        from .template_analyzer import analyze, AnalyzeError
        f = request.FILES.get('file')
        if not f:
            return error("Choose a PDF or image of the invoice design.")
        try:
            sug = analyze(f.read(), getattr(f, 'content_type', ''), f.name)
        except AnalyzeError as exc:
            return error(str(exc))
        except Exception:
            logger.warning("template analyze failed", exc_info=True)
            return error("Couldn't read that document. Try a PDF or a clear PNG/JPG of the invoice.")

        tid = 'c-' + secrets.token_hex(4)
        rel = f'template_sources/{tid}.png'
        os.makedirs(os.path.join(settings.MEDIA_ROOT, 'template_sources'), exist_ok=True)
        with open(os.path.join(settings.MEDIA_ROOT, rel), 'wb') as fh:
            fh.write(sug['preview_png'])

        from .template_registry import TEMPLATE_MAP
        base_name = TEMPLATE_MAP.get(sug['base'], {}).get('name', sug['base'])

        # With an OpenAI key, the whole upload goes to the AI designer, which
        # recreates the design as a real template (ai_template.py) in the background.
        from .ai_template import is_configured as ai_ready, start_job
        if ai_ready():
            ext = os.path.splitext(f.name)[1].lower() or '.bin'
            src_rel = f'template_sources/{tid}{ext}'
            f.seek(0)
            with open(os.path.join(settings.MEDIA_ROOT, src_rel), 'wb') as fh:
                for chunk in f.chunks():
                    fh.write(chunk)
            c = CustomTemplate(
                template_id=tid, name=sug['name'], base=sug['base'], accent=sug['accent'],
                desc=f"Designed from {f.name[:80]}", min_plan='plus', status='draft',
                source_name=f.name[:120], source_path=src_rel, preview_path=rel,
                mode='ai', ai_status='working', created_by=str(request.user.pk),
            ).save()
            start_job(tid)
            return success(_custom_template_data(c),
                           f"Reading “{f.name}” and designing the template — this takes about a minute.", 201)

        # No AI key: a PDF is copied exactly; its boxes, images and text keep their
        # positions, and the superadmin maps sample values to invoice fields.
        layout, mode, note = {}, 'base', ''
        if f.name.lower().endswith('.pdf') or getattr(f, 'content_type', '') == 'application/pdf':
            from .exact_template import extract, ExtractError
            try:
                f.seek(0)
                layout = extract(f.read(), tid)
                mode = 'exact'
            except ExtractError as exc:
                note = f" {exc} Using the closest built-in layout instead."
        else:
            note = " For an exact copy of the design, upload it as a PDF (in Word: File > Save As > PDF)."

        c = CustomTemplate(
            template_id=tid, name=sug['name'], base=sug['base'], accent=sug['accent'],
            desc=(f"Exact copy of {f.name[:80]}" if mode == 'exact'
                  else f"Based on {f.name[:80]} — {base_name} layout"),
            min_plan='plus', status='draft', source_name=f.name[:120], preview_path=rel,
            mode=mode, layout=layout, created_by=str(request.user.pk),
        ).save()
        data = _custom_template_data(c)
        data['signals'] = sug['signals']
        msg = (f"Copied “{f.name}”. Now click Map fields and mark the customer, items and totals."
               if mode == 'exact' else f"Read “{f.name}” — created a draft template.{note}")
        return success(data, msg, 201)


class CustomTemplatePublishView(APIView):
    """Apply (publish) a draft to Buy Invoice, or take it back to draft."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        from .models import CustomTemplate
        c = CustomTemplate.objects(template_id=pk, is_active=True).first()
        if not c:
            return error("Template not found.", status=404)
        live = request.data.get('publish', True) not in (False, 'false', 0, '0')
        c.status = 'published' if live else 'draft'
        c.save()
        try:
            from apps.authentication.models import AuditLog
            AuditLog.log(request.user, 'template_published' if live else 'template_unpublished',
                         f'{c.name} ({c.template_id})')
        except Exception:
            pass
        msg = (f"“{c.name}” is now in Buy Invoice." if live
               else f"“{c.name}” taken out of Buy Invoice (kept as a draft).")
        return success(_custom_template_data(c), msg)


class CustomTemplateLayoutView(APIView):
    """The copied page of an 'exact' template, for the field mapper."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def _get(self, pk):
        from .models import CustomTemplate
        c = CustomTemplate.objects(template_id=pk, is_active=True).first()
        return c if c and getattr(c, 'mode', 'base') == 'exact' else None

    def get(self, request, pk):
        from .exact_template import FIELDS, IMAGE_ROLES
        c = self._get(pk)
        if not c:
            return error("Template not found.", status=404)
        layout = dict(c.layout)
        layout['images'] = [dict(im, url=settings.MEDIA_URL + im['src']) for im in layout.get('images', [])]
        return success({
            'layout': layout,
            'fields': [{'key': k, 'label': v[0], 'group': v[1]} for k, v in FIELDS.items()],
            'image_roles': IMAGE_ROLES,
        })

    def put(self, request, pk):
        from .exact_template import FIELDS, IMAGE_ROLES
        c = self._get(pk)
        if not c:
            return error("Template not found.", status=404)
        fields = request.data.get('fields') or {}
        images = request.data.get('images') or {}
        if not isinstance(fields, dict) or not isinstance(images, dict):
            return error("Send fields and images as objects.")
        layout = dict(c.layout)
        for sp in layout.get('spans', []):
            if sp['id'] in fields:
                f = str(fields[sp['id']] or '')
                sp['field'] = f if (f in FIELDS or f in ('', 'hide')) else ''
        for im in layout.get('images', []):
            if im['id'] in images and images[im['id']] in IMAGE_ROLES:
                im['role'] = images[im['id']]
        c.layout = layout
        c.save()
        return success(_custom_template_data(c), "Field mapping saved.")


def _sample_invoice():
    """An unsaved invoice with realistic data, for template previews."""
    from .models import InvoiceItem
    now = datetime.utcnow()
    from datetime import timedelta
    items = [
        InvoiceItem(product_id='manual', product_name='Website design', description='Website design',
                    hsn_code='998314', unit='Nos', unit_price=25000, quantity=1, tax_rate=18, discount=0),
        InvoiceItem(product_id='manual', product_name='Hosting (1 year)', description='Hosting (1 year)',
                    hsn_code='998315', unit='Nos', unit_price=12500, quantity=1, tax_rate=18, discount=0),
    ]
    inv = Invoice(invoice_number='INV-2026-0001', customer_id='manual', customer_name='Acme Traders',
                  customer_email='accounts@acme.in', customer_address='45 Park Street, Kolkata 700016',
                  customer_gst='19AAACA1111A1Z1', customer_phone='+91 90000 11111',
                  customer_recipient='R. Kumar', cgst_rate=9, sgst_rate=9, igst_rate=0,
                  invoice_date=now, due_date=now + timedelta(days=30), items=items,
                  notes='Thank you for your business.', terms='Payment due within 30 days.',
                  signatory_name='Authorised Signatory', created_by='preview')
    inv.calculate_totals()
    return inv


class CustomTemplateRenderView(APIView):
    """The template filled with sample data — HTML for the View dialog,
    the mapper preview and the card. Works for drafts too."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def get(self, request, pk):
        from django.template.loader import render_to_string
        from .models import CustomTemplate
        from .exact_template import render, SAMPLE
        from .print_context import build_print_context
        from .web_views import _web_media
        c = CustomTemplate.objects(template_id=pk, is_active=True).first()
        if not c:
            return error("Template not found.", status=404)
        bp = BusinessProfile.objects(user_id=str(request.user.pk)).first()

        if getattr(c, 'mode', 'base') == 'ai':
            if getattr(c, 'ai_status', '') != 'ready':
                return error("This design is still being created." if c.ai_status == 'working'
                             else (c.ai_error or "This design isn't ready."), status=409)
            from .ai_template import render as ai_render, ai_context
            values = dict(SAMPLE)
            if bp and bp.logo_path:
                values['_logo'] = settings.MEDIA_URL + bp.logo_path
            html_src = c.ai_html
            if getattr(c, 'page_style', 'light') == 'dark':
                from .ai_template import darken
                html_src = darken(html_src)
            return success({'html': ai_render(html_src, ai_context(values)), 'kind': 'exact',
                            'w': 595.28, 'h': 841.89})

        if getattr(c, 'mode', 'base') == 'exact' and c.layout:
            values = dict(SAMPLE)
            if bp and bp.logo_path:
                values['_logo'] = settings.MEDIA_URL + bp.logo_path
            return success({'html': render(c.layout, values), 'kind': 'exact',
                            'w': c.layout.get('w'), 'h': c.layout.get('h')})

        # Built-in layout + this template's colours (drafts aren't in the
        # registry yet, so apply the custom spec here directly).
        inv = _sample_invoice()
        ctx = _web_media(build_print_context(inv, bp, None, c.base))
        spec = template_registry._custom_spec(c)
        for k in ('accent', 'secondary', 'text', 'muted', 'border', 'tint'):
            if spec.get(k):
                ctx[k] = spec[k]
        ctx['style_id'] = spec.get('base') or c.base
        return success({'html': render_to_string('invoices/_invoice_doc.html', ctx), 'kind': 'base'})


class InvoiceRenderView(APIView):
    """Server-rendered page for invoices on an exact-copy template (detail page)."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request, pk):
        from .print_context import build_print_context
        from .exact_template import page_html
        from .web_views import _web_media
        invoice = _invoice_qs(request.user).filter(pk=pk).first()
        if not invoice:
            return error("Invoice not found.", status=404)
        bp = BusinessProfile.objects(user_id=invoice.created_by).first()
        ctx = _web_media(build_print_context(invoice, bp, None, invoice.template_style or 'classic'))
        return success({'html': page_html(ctx, invoice)})


class CustomTemplateRegenerateView(APIView):
    """Run the AI designer again for an uploaded template (e.g. after a failure)."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        from .models import CustomTemplate
        from .ai_template import is_configured, start_job
        c = CustomTemplate.objects(template_id=pk, is_active=True).first()
        if not c or getattr(c, 'mode', '') != 'ai' or not getattr(c, 'source_path', ''):
            return error("Template not found.", status=404)
        if not is_configured():
            return error("OPENAI_API_KEY is not set in .env on the server.")
        if c.ai_status == 'working':
            return error("It's already being designed.")
        c.ai_status, c.ai_error = 'working', ''
        c.save()
        start_job(c.template_id)
        return success(_custom_template_data(c), "Designing again — this takes about a minute.")


class LiveTemplateRenderView(APIView):
    """Draw an uploaded/AI template with the editor's current, unsaved values,
    so the New/Edit Invoice preview shows the real design while typing."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def post(self, request):
        from .exact_template import FIELDS, render as exact_render
        from .ai_template import render as ai_render, ai_context
        tid = str(request.data.get('template_style') or '')
        layout, ai_html = template_registry.exact_layout(tid), template_registry.ai_page(tid)
        if not layout and not ai_html:
            return error("Not an uploaded template.", status=404)
        tpl_err = _check_template_allowed(request, tid)
        if tpl_err is not None:
            return tpl_err

        raw = request.data.get('values') or {}
        if not isinstance(raw, dict):
            return error("values must be an object.")
        clip = lambda v, n=500: str(v if v is not None else '')[:n]
        values = {k: clip(raw.get(k)) for k in FIELDS}
        values['tot.words'] = clip(raw.get('tot.words'), 300)
        # Images: only this site's media files or an inline image the user just picked.
        def img(v):
            v = clip(v, 2_000_000)
            return v if (v.startswith(settings.MEDIA_URL) or v.startswith('data:image/')) else ''
        values['_logo'], values['_sig'] = img(raw.get('_logo')), img(raw.get('_sig'))
        items = raw.get('_items') if isinstance(raw.get('_items'), list) else []
        values['_items'] = [{k: clip(it.get(k), 300) for k in FIELDS if k.startswith('item.')}
                            for it in items[:200] if isinstance(it, dict)]
        extra = raw.get('_extra') if isinstance(raw.get('_extra'), dict) else {}
        values['_extra'] = {str(k)[:41]: clip(val, 2000) for k, val in list(extra.items())[:40]}
        html = ai_render(ai_html, ai_context(values)) if ai_html else exact_render(layout, values)
        return success({'html': html})


class CustomTemplatePromptView(APIView):
    """The ready-made prompt for the 'Copy prompt' flow."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        from .ai_template import manual_prompt
        return success({'prompt': manual_prompt()})


class CustomTemplateFromHtmlView(APIView):
    """Create a draft template from HTML the superadmin got from ChatGPT/Gemini
    (pasted, or uploaded as .html/.txt). No AI runs here — the HTML is checked
    exactly like server-generated designs before it is stored."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def post(self, request):
        import secrets
        from .models import CustomTemplate
        from .ai_template import sanitize, render as ai_render, ai_context, extract_fields, AIError
        from .exact_template import SAMPLE
        html = request.data.get('html') or ''
        up = request.FILES.get('file')
        if up:
            if up.size > 1024 * 1024:
                return error("That file is too large (max 1 MB of HTML).")
            if not up.name.lower().endswith(('.html', '.htm', '.txt')):
                return error("Upload the .html (or .txt) file the AI gave you.")
            html = up.read().decode('utf-8', errors='replace')
        html = str(html)
        if len(html) > 1024 * 1024:
            return error("That HTML is too large (max 1 MB).")
        if not html.strip():
            return error("Paste the HTML the AI gave you, or upload it as a file.")
        try:
            clean = sanitize(html)
            ai_render(clean, ai_context(SAMPLE))      # must render with sample data
        except AIError as exc:
            return error(str(exc).replace('Try again.', 'Ask the AI to fix that and paste it again.'))
        except Exception as exc:
            return error(f"That HTML couldn't be used as a template ({type(exc).__name__}). "
                         "Ask the AI to follow the prompt exactly and paste it again.")
        name = (str(request.data.get('name') or '').strip() or 'My Design')[:60]
        m = re.search(r'#[0-9a-fA-F]{6}', clean)
        c = CustomTemplate(
            template_id='c-' + secrets.token_hex(4), name=name, base='classic',
            accent=(m.group(0).upper() if m else '#C1121F'), desc='Designed with your own AI (copy prompt)',
            min_plan='plus', status='draft', mode='ai', ai_status='ready', ai_html=clean,
            ai_fields=extract_fields(clean), created_by=str(request.user.pk),
        ).save()
        try:
            from apps.authentication.models import AuditLog
            AuditLog.log(request.user, 'template_added', f'{c.name} ({c.template_id}) from pasted HTML')
        except Exception:
            pass
        n = len(c.ai_fields)
        return success(_custom_template_data(c),
                       f"“{c.name}” created as a draft{f' with {n} editable field' + ('s' if n != 1 else '') if n else ''}. "
                       "Click View to check it, then Apply.", 201)
