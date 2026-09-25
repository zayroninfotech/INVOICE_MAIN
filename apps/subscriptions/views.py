from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.conf import settings
from apps.authentication.authentication import MongoJWTAuthentication
from apps.authentication.permissions import IsSuperAdmin
from apps.authentication.models import User
from .models import Subscription, PlanSettings
from utils.response import success, error
from datetime import datetime, timedelta
import hmac
import hashlib


def _get_or_create_sub(user_id):
    sub = Subscription.objects(user_id=str(user_id)).first()
    if not sub:
        sub = Subscription(user_id=str(user_id)).save()
    return sub


def _sub_data(sub):
    from django.conf import settings as s
    limits = s.PLAN_LIMITS.get(sub.plan, s.PLAN_LIMITS['free'])
    daily = limits.get('invoices_per_day')
    monthly = limits.get('invoices_per_month')
    sub._reset_if_needed()
    return {
        'plan': sub.plan,
        'status': sub.status,
        'start_date': sub.start_date.isoformat() if sub.start_date else None,
        'end_date': sub.end_date.isoformat() if sub.end_date else None,
        'invoices_used_today': sub.invoices_used_today,
        'invoices_used_month': sub.invoices_used_month,
        'daily_limit': daily,
        'monthly_limit': monthly,
        'razorpay_payment_id': sub.razorpay_payment_id,
        'amount_paid': float(sub.amount_paid) if sub.amount_paid else 0,
    }


class MySubscriptionView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def get(self, request):
        sub = _get_or_create_sub(request.user.pk)
        return success(_sub_data(sub))


class PricingConfigView(APIView):
    """Public endpoint — returns plan prices and Razorpay key_id."""
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        ps = PlanSettings.get()
        return success({
            'plus_price': ps.plus_price,
            'pro_price': ps.pro_price,
            'unlimited_price': ps.unlimited_price,
            'razorpay_key_id': ps.razorpay_key_id if ps.is_payments_enabled else '',
            'is_payments_enabled': ps.is_payments_enabled,
            'plans': settings.PLAN_LIMITS,
        })


class CreateOrderView(APIView):
    """Create Razorpay order for Plus/Pro/Unlimited upgrade."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        plan = request.data.get('plan', '')
        if plan not in settings.PAID_PLANS:
            return error("Invalid plan.")
        ps = PlanSettings.get()
        if not ps.is_payments_enabled or not ps.razorpay_key_id or not ps.razorpay_key_secret:
            return error("Online payments are not configured yet. Please contact support.")
        try:
            import razorpay
            client = razorpay.Client(auth=(ps.razorpay_key_id, ps.razorpay_key_secret))
            price_map = {'plus': ps.plus_price, 'pro': ps.pro_price, 'unlimited': ps.unlimited_price}
            amount = price_map[plan]
            order = client.order.create({
                'amount': amount,
                'currency': 'INR',
                'receipt': f'sub_{request.user.pk}_{plan}',
                'notes': {'user_id': str(request.user.pk), 'plan': plan},
            })
            return success({
                'order_id': order['id'],
                'amount': amount,
                'currency': 'INR',
                'key_id': ps.razorpay_key_id,
                'plan': plan,
            })
        except ImportError:
            return error("Payment library not installed. Run: pip install razorpay")
        except Exception as e:
            return error(f"Failed to create order: {str(e)}")


class VerifyPaymentView(APIView):
    """Verify Razorpay payment signature and activate subscription."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        order_id = request.data.get('razorpay_order_id', '')
        payment_id = request.data.get('razorpay_payment_id', '')
        signature = request.data.get('razorpay_signature', '')
        plan = request.data.get('plan', '')

        if not all([order_id, payment_id, signature, plan]):
            return error("Missing payment details.")

        ps = PlanSettings.get()
        expected = hmac.new(
            ps.razorpay_key_secret.encode(),
            f"{order_id}|{payment_id}".encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, signature):
            return error("Payment verification failed. Invalid signature.")

        sub = _get_or_create_sub(request.user.pk)
        sub.plan = plan
        sub.status = 'active'
        sub.razorpay_payment_id = payment_id
        sub.razorpay_order_id = order_id
        sub.start_date = datetime.utcnow()
        sub.end_date = datetime.utcnow() + timedelta(days=30)
        ps_prices = {'plus': ps.plus_price, 'pro': ps.pro_price, 'unlimited': ps.unlimited_price}
        sub.amount_paid = ps_prices.get(plan, 0) / 100
        sub.save()
        sub.reset_usage_counters()
        return success(_sub_data(sub), f"Upgraded to {plan.capitalize()} successfully!")


# ── Superadmin: Subscription Management ──────────────────────────────────────

class SuperadminSubscriptionsView(APIView):
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        plan_filter = request.query_params.get('plan', '')
        search = request.query_params.get('search', '')
        page = int(request.query_params.get('page', 1))
        page_size = 20

        users_qs = User.objects()
        if search:
            users_qs = users_qs.filter(__raw__={'$or': [
                {'username': {'$regex': search, '$options': 'i'}},
                {'email': {'$regex': search, '$options': 'i'}},
            ]})

        all_users = list(users_qs.order_by('-created_at'))
        user_ids = [str(u.pk) for u in all_users]

        # Fetch all subscriptions in one query
        subs_map = {
            s.user_id: s for s in Subscription.objects(user_id__in=user_ids)
        }

        results = []
        for u in all_users:
            uid = str(u.pk)
            sub = subs_map.get(uid)
            plan = sub.plan if sub else 'free'
            if plan_filter and plan != plan_filter:
                continue
            sub._reset_if_needed() if sub else None
            results.append({
                'user_id': uid,
                'username': u.username,
                'email': u.email,
                'role': u.role,
                'is_active': u.is_active,
                'plan': plan,
                'status': sub.status if sub else 'active',
                'invoices_today': sub.invoices_used_today if sub else 0,
                'invoices_month': sub.invoices_used_month if sub else 0,
                'end_date': sub.end_date.strftime('%d %b %Y') if sub and sub.end_date else '—',
                'amount_paid': float(sub.amount_paid) if sub else 0,
            })

        total = len(results)
        offset = (page - 1) * page_size
        results = results[offset:offset + page_size]

        return success({'total': total, 'page': page, 'results': results})


class SuperadminChangePlanView(APIView):
    """Superadmin manually change a user's plan."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        user = User.objects(pk=pk).first()
        if not user:
            return error("User not found.", status=404)
        plan = request.data.get('plan', 'free')
        if plan not in ('free',) + settings.PAID_PLANS:
            return error("Invalid plan.")
        sub = _get_or_create_sub(pk)
        sub.plan = plan
        sub.status = 'active'
        sub.start_date = datetime.utcnow()
        if plan == 'free':
            sub.end_date = None
            sub.amount_paid = 0
            sub.purchased_templates = []
        else:
            sub.end_date = datetime.utcnow() + timedelta(days=30)
        sub.save()
        sub.reset_usage_counters()
        return success(_sub_data(sub), f"Plan changed to {plan.capitalize()}.")


class SuperadminPlanSettingsView(APIView):
    """Superadmin: configure Razorpay + pricing."""
    authentication_classes = [MongoJWTAuthentication]
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        ps = PlanSettings.get()
        return success({
            'razorpay_key_id': ps.razorpay_key_id,
            'razorpay_key_secret': ps.razorpay_key_secret,
            'plus_price': ps.plus_price,
            'pro_price': ps.pro_price,
            'unlimited_price': ps.unlimited_price,
            'is_payments_enabled': ps.is_payments_enabled,
            'bank_name': ps.bank_name,
            'bank_account': ps.bank_account,
            'bank_ifsc': ps.bank_ifsc,
            'bank_holder': ps.bank_holder,
        })

    def put(self, request):
        ps = PlanSettings.get()
        fields = ['razorpay_key_id', 'razorpay_key_secret', 'bank_name',
                  'bank_account', 'bank_ifsc', 'bank_holder']
        for f in fields:
            if f in request.data:
                setattr(ps, f, str(request.data[f]).strip())
        for price_field in ['plus_price', 'pro_price', 'unlimited_price']:
            if price_field in request.data:
                setattr(ps, price_field, int(request.data[price_field]))
        if 'is_payments_enabled' in request.data:
            ps.is_payments_enabled = bool(request.data['is_payments_enabled'])
        ps.save()
        return success({
            'razorpay_key_id': ps.razorpay_key_id,
            'plus_price': ps.plus_price,
            'pro_price': ps.pro_price,
            'unlimited_price': ps.unlimited_price,
            'is_payments_enabled': ps.is_payments_enabled,
            'bank_name': ps.bank_name,
        }, "Settings saved.")
