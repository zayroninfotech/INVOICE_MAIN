from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.conf import settings
from apps.authentication.authentication import MongoJWTAuthentication
from apps.invoices.models import Invoice
from apps.customers.models import Customer
from apps.products.models import Product
from apps.authentication.models import User
from apps.subscriptions.entitlements import get_plan_for_request
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
        all_customers = Customer.objects(is_active=True) if is_super else Customer.objects(created_by=str(user.pk), is_active=True)
        all_products = Product.objects(is_active=True) if is_super else Product.objects(created_by=str(user.pk), is_active=True)

        invoices = list(all_invoices.only(
            'invoice_number', 'customer_name', 'grand_total', 'status',
            'approval_status', 'invoice_date', 'due_date'))

        def naive(dt):
            return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt

        def month_key(dt):
            return (dt.year, dt.month)

        this_m = month_key(now)
        last_m = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
        trend_keys = []
        y, m = now.year, now.month
        for _ in range(12):
            trend_keys.append((y, m))
            y, m = (y - 1, 12) if m == 1 else (y, m - 1)
        trend_keys.reverse()
        trend = {k: {'billed': 0.0, 'collected': 0.0} for k in trend_keys}

        billed_this = billed_last = collected_total = outstanding = overdue_amt = 0.0
        count_this = overdue_count = outstanding_count = paid_count = cancelled_count = 0
        aging = {'current': [0, 0.0], '1_30': [0, 0.0], '31_60': [0, 0.0], '61_90': [0, 0.0], '90_plus': [0, 0.0]}
        approvals = {'In Process': 0, 'Approved': 0, 'Disapproved': 0}
        attention = []

        for inv in invoices:
            amt = float(inv.grand_total or 0)
            approvals[inv.approval_status or 'In Process'] = approvals.get(inv.approval_status or 'In Process', 0) + 1
            if inv.status == 'Cancelled':
                cancelled_count += 1
                continue
            idate = naive(inv.invoice_date)
            if idate:
                k = month_key(idate)
                if k == this_m:
                    billed_this += amt
                    count_this += 1
                elif k == last_m:
                    billed_last += amt
                if k in trend:
                    trend[k]['billed'] += amt
                    if inv.status == 'Paid':
                        trend[k]['collected'] += amt
            if inv.status == 'Paid':
                paid_count += 1
                collected_total += amt
                continue

            outstanding += amt
            outstanding_count += 1
            due = naive(inv.due_date)
            days_late = (now - due).days if due else 0
            if due and due < now and days_late >= 1:
                overdue_amt += amt
                overdue_count += 1
                bucket = '1_30' if days_late <= 30 else '31_60' if days_late <= 60 else '61_90' if days_late <= 90 else '90_plus'
            else:
                bucket = 'current'
            aging[bucket][0] += 1
            aging[bucket][1] += amt
            if due and days_late >= -7:
                attention.append({
                    'id': str(inv.pk),
                    'invoice_number': inv.invoice_number,
                    'customer_name': inv.customer_name,
                    'amount': round(amt, 2),
                    'due_date': due.isoformat(),
                    'days_late': days_late,
                })

        attention.sort(key=lambda a: (-a['days_late'], -a['amount']))
        billed_total = collected_total + outstanding
        change = None
        if billed_last:
            change = round((billed_this - billed_last) / billed_last * 100, 1)

        data = {
            'role': user.role,
            'kpi': {
                'billed_this_month': round(billed_this, 2),
                'invoices_this_month': count_this,
                'billed_change_pct': change,
                'collected': round(collected_total, 2),
                'collection_rate': round(collected_total / billed_total * 100, 1) if billed_total else 0,
                'outstanding': round(outstanding, 2),
                'outstanding_count': outstanding_count,
                'overdue': round(overdue_amt, 2),
                'overdue_count': overdue_count,
            },
            'counts': {
                'invoices': len(invoices),
                'paid': paid_count,
                'cancelled': cancelled_count,
                'customers': all_customers.count(),
                'products': all_products.count(),
            },
            'aging': {k: {'count': v[0], 'amount': round(v[1], 2)} for k, v in aging.items()},
            'approvals': approvals,
            'trend': [{'label': datetime(y, m, 1).strftime('%b'), 'year': y,
                       'billed': round(trend[(y, m)]['billed'], 2),
                       'collected': round(trend[(y, m)]['collected'], 2)} for y, m in trend_keys],
            'attention': attention[:6],
        }

        if is_super:
            active = User.objects(is_active=True)
            data['users'] = {
                'total': active.count(),
                'superadmins': active.filter(role='superadmin').count(),
                'admins': active.filter(role='admin').count(),
                'users': active.filter(role='user').count(),
                'new_this_month': active.filter(created_at__gte=month_start).count(),
            }
        else:
            plan, sub = get_plan_for_request(request)
            limits = settings.PLAN_LIMITS.get(plan, {})
            sub._reset_if_needed() if sub else None
            data['plan'] = {
                'slug': plan,
                'label': limits.get('label', plan.capitalize()),
                'used_today': sub.invoices_used_today if sub else None,
                'used_month': sub.invoices_used_month if sub else None,
                'daily_limit': limits.get('invoices_per_day'),
                'monthly_limit': limits.get('invoices_per_month'),
            }

        return success(data)
