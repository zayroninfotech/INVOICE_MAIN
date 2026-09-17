from django.conf import settings
from .models import Subscription


def get_plan_for_request(request):
    """Returns (plan_slug, subscription_doc_or_None)."""
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        sub = Subscription.objects(user_id=str(user.pk)).first()
        if not sub:
            sub = Subscription(user_id=str(user.pk))
            sub.save()
        if sub.plan == 'premium':
            sub.plan = 'unlimited'
            sub.save()
        return sub.plan, sub
    return 'anonymous', None


def can_create_invoice(request):
    """
    Returns (ok, reason, plan, sub) in all cases:
      ok=True,  reason=None,           plan=slug, sub=sub_or_None  — allowed
      ok=False, reason='session_limit'|'daily_limit'|'monthly_limit',
                plan=slug, sub=sub_or_None                         — blocked
    """
    user = getattr(request, 'user', None)
    if user and getattr(user, 'role', '') == 'superadmin':
        return True, None, 'unlimited', None

    plan, sub = get_plan_for_request(request)
    limits = settings.PLAN_LIMITS[plan]

    if plan == 'anonymous':
        _ensure_session(request)
        used = request.session.get('anon_invoices_used', 0)
        if used >= limits['invoices_per_session']:
            return False, 'session_limit', plan, None
        return True, None, plan, None

    sub._reset_if_needed()
    daily = limits.get('invoices_per_day')
    monthly = limits.get('invoices_per_month')
    if daily is not None and sub.invoices_used_today >= daily:
        return False, 'daily_limit', plan, sub
    if monthly is not None and sub.invoices_used_month >= monthly:
        return False, 'monthly_limit', plan, sub
    return True, None, plan, sub


def increment_usage(request, plan, sub):
    if plan == 'anonymous':
        _ensure_session(request)
        request.session['anon_invoices_used'] = request.session.get('anon_invoices_used', 0) + 1
        request.session.modified = True
    elif sub is not None:
        sub.increment_usage()


def _ensure_session(request):
    if not request.session.session_key:
        request.session.create()
    request.session.set_expiry(0)


def plan_for_user_id(user_id):
    """Resolve a subscription plan slug from a raw user id (works outside the
    request cycle, e.g. Celery tasks / PDF regeneration)."""
    sub = Subscription.objects(user_id=str(user_id)).first()
    if not sub:
        return 'free'
    if sub.plan == 'premium':
        return 'unlimited'
    return sub.plan or 'free'
