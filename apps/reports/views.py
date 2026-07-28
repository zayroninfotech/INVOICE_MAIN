from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.authentication.authentication import MongoJWTAuthentication
from apps.invoices.models import Invoice
from apps.payments.models import Payment
from utils.response import success, error
from datetime import datetime
import csv
from django.http import HttpResponse


class SalesReportView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_id = str(user.pk)
        date_from = request.query_params.get('from')
        date_to = request.query_params.get('to')

        queryset = Invoice.objects() if getattr(user, 'role', '') == 'superadmin' else Invoice.objects(created_by=user_id)
        try:
            if date_from:
                queryset = queryset.filter(invoice_date__gte=datetime.fromisoformat(date_from))
            if date_to:
                queryset = queryset.filter(invoice_date__lte=datetime.fromisoformat(date_to))
        except ValueError:
            return error("Invalid date format. Use YYYY-MM-DD.")

        invoices = list(queryset.order_by('-invoice_date'))
        total_amount = sum(float(inv.grand_total) for inv in invoices)
        total_tax = sum(float(inv.tax_amount) for inv in invoices)
        paid_count = sum(1 for inv in invoices if inv.status == 'Paid')

        data = [{
            'invoice_number': inv.invoice_number,
            'customer_name': inv.customer_name,
            'invoice_date': inv.invoice_date.strftime('%Y-%m-%d') if inv.invoice_date else '',
            'due_date': inv.due_date.strftime('%Y-%m-%d') if inv.due_date else '',
            'subtotal': float(inv.subtotal),
            'tax_amount': float(inv.tax_amount),
            'grand_total': float(inv.grand_total),
            'status': inv.status,
            'currency': inv.currency,
        } for inv in invoices]

        return success({
            'summary': {
                'total_invoices': len(invoices),
                'paid_invoices': paid_count,
                'total_amount': round(total_amount, 2),
                'total_tax': round(total_tax, 2),
            },
            'invoices': data,
        })


class SalesReportCSVView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_id = str(user.pk)
        date_from = request.query_params.get('from')
        date_to = request.query_params.get('to')

        queryset = Invoice.objects() if getattr(user, 'role', '') == 'superadmin' else Invoice.objects(created_by=user_id)
        if date_from:
            queryset = queryset.filter(invoice_date__gte=datetime.fromisoformat(date_from))
        if date_to:
            queryset = queryset.filter(invoice_date__lte=datetime.fromisoformat(date_to))

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="sales_report.csv"'
        writer = csv.writer(response)
        writer.writerow(['Invoice No', 'Customer', 'Date', 'Due Date', 'Subtotal', 'Tax', 'Total', 'Status'])
        for inv in queryset.order_by('-invoice_date'):
            writer.writerow([
                inv.invoice_number, inv.customer_name,
                inv.invoice_date.strftime('%Y-%m-%d') if inv.invoice_date else '',
                inv.due_date.strftime('%Y-%m-%d') if inv.due_date else '',
                float(inv.subtotal), float(inv.tax_amount), float(inv.grand_total), inv.status,
            ])
        return response


class MonthlyRevenueView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        user_id = str(user.pk)
        year = int(request.query_params.get('year', datetime.utcnow().year))
        if getattr(user, 'role', '') == 'superadmin':
            payments = Payment.objects(
                payment_date__gte=datetime(year, 1, 1),
                payment_date__lte=datetime(year, 12, 31))
        else:
            payments = Payment.objects(created_by=user_id,
                                   payment_date__gte=datetime(year, 1, 1),
                                   payment_date__lte=datetime(year, 12, 31))
        monthly = {i: 0.0 for i in range(1, 13)}
        for p in payments:
            monthly[p.payment_date.month] += float(p.amount)
        return success({
            'year': year,
            'monthly_revenue': [
                {'month': m, 'amount': round(v, 2)} for m, v in monthly.items()
            ],
        })
