from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from django.http import FileResponse
from django.conf import settings
from apps.authentication.authentication import MongoJWTAuthentication
from apps.authentication.permissions import IsReadOnlyForUser, IsAdminOrSuperAdmin
from apps.authentication.models import BusinessProfile
from apps.subscriptions.entitlements import can_create_invoice, increment_usage, get_plan_for_request
from .models import Invoice
from .serializers import InvoiceSerializer, InvoiceListSerializer, InvoiceDetailSerializer
from .pdf_generator import generate_invoice_pdf
from . import template_registry
from .tasks import send_invoice_email_task
from utils.response import success, error
import os


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


class InvoiceListCreateView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsReadOnlyForUser]

    def get(self, request):
        status_filter = request.query_params.get('status', '')
        search = request.query_params.get('search', '')
        queryset = _invoice_qs(request.user)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if search:
            queryset = queryset.filter(
                __raw__={'$or': [
                    {'invoice_number': {'$regex': search, '$options': 'i'}},
                    {'customer_name': {'$regex': search, '$options': 'i'}},
                ]}
            )
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
        bp = BusinessProfile.objects(user_id=invoice.created_by).first()
        pdf_path = generate_invoice_pdf(invoice, business_profile=bp)
        invoice.pdf_path = pdf_path
        invoice.save()
        send_invoice_email_task.delay(str(invoice.pk), invoice.customer_email, pdf_path)
        return success(message=f"Invoice email queued for {invoice.customer_email}.")


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
