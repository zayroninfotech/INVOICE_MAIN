from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.http import FileResponse, HttpResponse
from django.conf import settings
from apps.authentication.authentication import MongoJWTAuthentication
from apps.authentication.permissions import IsReadOnlyForUser, IsAdminOrSuperAdmin
from apps.authentication.models import BusinessProfile
from apps.subscriptions.entitlements import can_create_invoice, increment_usage, get_plan_for_request
from .models import Invoice
from .serializers import InvoiceSerializer, InvoiceListSerializer, InvoiceDetailSerializer
from .pdf_generator import generate_invoice_pdf
from . import template_registry, report_export
from .emailer import send_invoice_email, approval_url, MailNotConfigured
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
            return error(f"Could not send the email: {exc}", status=502)

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
        templates = []
        for t in template_registry.templates_payload():
            min_plan = t['effective_min_plan']
            unlocked = user_rank >= PLAN_RANK.get(min_plan, 0) and not t['blocked']
            templates.append({
                'id': t['id'],
                'name': t['name'],
                'desc': t['desc'],
                'min_plan': min_plan,
                'badge': t.get('badge', '#F8FAFC'),
                'blocked': t['blocked'],
                'unlocked': unlocked,
            })
        return success({
            'plan': plan,
            'templates': templates,
            'sections': SECTION_LABELS,
            'default_section_order': template_registry.SECTIONS,
        })
