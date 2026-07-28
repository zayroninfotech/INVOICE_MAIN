from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from apps.authentication.authentication import MongoJWTAuthentication
from apps.invoices.models import Invoice
from apps.customers.models import Customer
from apps.products.models import Product
from apps.payments.models import Payment
from apps.authentication.models import User
from utils.response import success
from datetime import datetime


class DashboardStatsView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        now = datetime.utcnow()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        is_super = getattr(user, 'role', '') == 'superadmin'

        # Scope queries
        all_invoices = Invoice.objects() if is_super else Invoice.objects(created_by=str(user.pk))
        all_payments = Payment.objects() if is_super else Payment.objects(created_by=str(user.pk))
        all_customers = Customer.objects(is_active=True) if is_super else Customer.objects(created_by=str(user.pk), is_active=True)
        all_products = Product.objects(is_active=True) if is_super else Product.objects(created_by=str(user.pk), is_active=True)

        paid = all_invoices.filter(status='Paid')
        pending = all_invoices.filter(status__in=['Draft', 'Sent', 'Partial'])
        overdue = all_invoices.filter(status='Overdue')

        total_revenue = sum(float(p.amount) for p in all_payments)
        monthly_revenue = sum(
            float(p.amount) for p in all_payments.filter(payment_date__gte=month_start)
        )

        recent_invoices = all_invoices.order_by('-created_at').limit(5)
        recent_data = [{
            'id': str(inv.pk),
            'invoice_number': inv.invoice_number,
            'customer_name': inv.customer_name,
            'grand_total': float(inv.grand_total),
            'status': inv.status,
            'invoice_date': inv.invoice_date.isoformat() if inv.invoice_date else None,
        } for inv in recent_invoices]

        data = {
            'total_invoices': all_invoices.count(),
            'paid_invoices': paid.count(),
            'pending_invoices': pending.count(),
            'overdue_invoices': overdue.count(),
            'total_revenue': round(total_revenue, 2),
            'monthly_revenue': round(monthly_revenue, 2),
            'total_customers': all_customers.count(),
            'total_products': all_products.count(),
            'recent_invoices': recent_data,
            'role': user.role,
        }

        # Extra stats for superadmin
        if is_super:
            data['total_users'] = User.objects(is_active=True).count()
            data['total_admins'] = User.objects(role='admin', is_active=True).count()
            data['total_regular_users'] = User.objects(role='user', is_active=True).count()

        return success(data)
