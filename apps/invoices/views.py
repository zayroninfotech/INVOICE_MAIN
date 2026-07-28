from rest_framework.views import APIView
from django.http import FileResponse
from django.conf import settings
from apps.authentication.authentication import MongoJWTAuthentication
from apps.authentication.permissions import IsReadOnlyForUser, IsAdminOrSuperAdmin
from apps.authentication.models import BusinessProfile
from .models import Invoice
from .serializers import InvoiceSerializer, InvoiceListSerializer, InvoiceDetailSerializer
from .pdf_generator import generate_invoice_pdf
from .tasks import send_invoice_email_task
from utils.response import success, error
import os


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
        # Superadmin has no limits
        if getattr(request.user, 'role', '') != 'superadmin':
            from apps.subscriptions.models import Subscription
            sub = Subscription.objects(user_id=str(request.user.pk)).first()
            if not sub:
                sub = Subscription(user_id=str(request.user.pk)).save()
            can, reason = sub.can_create_invoice()
            if not can:
                return error(reason, {"upgrade_required": True}, status=402)

        serializer = InvoiceSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return error("Validation failed.", serializer.errors)
        invoice = serializer.save()

        # Increment usage counter
        if getattr(request.user, 'role', '') != 'superadmin':
            sub.increment_usage()

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
