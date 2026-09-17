import json
import re
from functools import wraps
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import check_password
from .models import User, AuditLog, TemplateBlock, TemplateConfig
from apps.subscriptions.models import Subscription
from apps.invoices.models import Invoice


def _get_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        uid = request.session.get('zadmin_uid')
        if not uid:
            return redirect('/z-admin/login/')
        try:
            user = User.objects.get(id=uid)
            if user.role not in ('superadmin', 'admin'):
                return redirect('/')
            request.admin_user = user
        except Exception:
            return redirect('/z-admin/login/')
        return view_func(request, *args, **kwargs)
    return wrapper


def admin_login(request):
    if request.method == 'GET':
        return render(request, 'admin_login.html')
    try:
        body = json.loads(request.body)
        identifier = body.get('identifier', '').strip()
        password   = body.get('password', '')
        user = (
            User.objects(email=identifier, is_active=True).first() or
            User.objects(username=identifier, is_active=True).first()
        )
        if not user or not check_password(password, user.password):
            return JsonResponse({'error': 'Invalid credentials.'}, status=401)
        if user.role not in ('superadmin', 'admin'):
            return JsonResponse({'error': 'Your account does not have admin access.'}, status=403)
        request.session['zadmin_uid'] = str(user.id)
        request.session.set_expiry(8 * 3600)
        AuditLog.log(user, 'admin_login', f'Admin login from {_get_ip(request)}', _get_ip(request))
        return JsonResponse({'ok': True})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


def admin_logout(request):
    request.session.pop('zadmin_uid', None)
    return redirect('/z-admin/login/')


@admin_required
def admin_dashboard(request):
    return render(request, 'admin_panel.html', {'admin_user': request.admin_user})


@admin_required
def admin_stats(request):
    total_users     = User.objects.count()
    active_users    = User.objects.filter(is_active=True).count()
    total_invoices  = Invoice.objects.count()
    paid_invoices   = Invoice.objects.filter(status='Paid').count()
    from mongoengine.queryset.visitor import Q
    revenue = sum(float(inv.grand_total) for inv in Invoice.objects.filter(status='Paid'))
    pro_subs = Subscription.objects.filter(plan__nin=['free']).count()
    return JsonResponse({
        'total_users': total_users,
        'active_users': active_users,
        'blocked_users': total_users - active_users,
        'total_invoices': total_invoices,
        'paid_invoices': paid_invoices,
        'revenue': round(revenue, 2),
        'pro_subscriptions': pro_subs,
    })


@csrf_exempt
@admin_required
def admin_users_api(request):
    if request.method == 'GET':
        users = list(User.objects.all().order_by('-created_at'))
        subs  = {s.user_id: s for s in Subscription.objects.all()}
        data  = []
        for u in users:
            sub = subs.get(str(u.id))
            data.append({
                'id':         str(u.id),
                'username':   u.username,
                'email':      u.email,
                'role':       u.role,
                'is_active':  u.is_active,
                'plan':       sub.plan if sub else 'free',
                'created_at': u.created_at.strftime('%Y-%m-%d %H:%M') if u.created_at else '',
            })
        return JsonResponse({'users': data})

    if request.method == 'POST':
        body   = json.loads(request.body)
        action = body.get('action')
        uid    = body.get('user_id')
        try:
            user = User.objects.get(id=uid)
        except Exception:
            return JsonResponse({'error': 'User not found'}, status=404)

        if action == 'toggle_active':
            user.is_active = not user.is_active
            user.save()
            verb = 'Unblocked' if user.is_active else 'Blocked'
            AuditLog.log(request.admin_user, 'user_toggle', f'{verb} user {user.email}', _get_ip(request))
            return JsonResponse({'is_active': user.is_active})

        if action == 'change_role':
            new_role = body.get('role')
            if new_role not in ('user', 'admin', 'superadmin'):
                return JsonResponse({'error': 'Invalid role'}, status=400)
            old_role, user.role = user.role, new_role
            user.save()
            AuditLog.log(request.admin_user, 'role_change',
                         f'{user.email}: {old_role} → {new_role}', _get_ip(request))
            return JsonResponse({'role': user.role})

        if action == 'change_plan':
            new_plan = body.get('plan')
            if new_plan not in ('free', 'plus', 'pro', 'unlimited', 'premium'):
                return JsonResponse({'error': 'Invalid plan'}, status=400)
            sub = Subscription.objects.filter(user_id=str(user.id)).first()
            if not sub:
                sub = Subscription(user_id=str(user.id))
            old_plan, sub.plan = sub.plan, new_plan
            sub.save()
            AuditLog.log(request.admin_user, 'plan_change',
                         f'{user.email}: {old_plan} → {new_plan}', _get_ip(request))
            return JsonResponse({'plan': sub.plan})

        return JsonResponse({'error': 'Unknown action'}, status=400)


@admin_required
def admin_audit_api(request):
    page     = max(1, int(request.GET.get('page', 1)))
    per_page = 50
    skip     = (page - 1) * per_page
    logs     = AuditLog.objects.order_by('-created_at').skip(skip).limit(per_page)
    total    = AuditLog.objects.count()
    data     = [{
        'actor':      l.actor_email,
        'action':     l.action,
        'detail':     l.detail,
        'ip':         l.ip,
        'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S') if l.created_at else '',
    } for l in logs]
    return JsonResponse({'logs': data, 'total': total,
                         'pages': max(1, (total + per_page - 1) // per_page), 'page': page})


@admin_required
def admin_invoices_api(request):
    page     = max(1, int(request.GET.get('page', 1)))
    per_page = 50
    skip     = (page - 1) * per_page
    status   = request.GET.get('status', '')
    qs       = Invoice.objects.filter(status=status) if status else Invoice.objects.all()
    invoices = list(qs.order_by('-created_at').skip(skip).limit(per_page))
    total    = qs.count()
    # created_by stores the owning user's id, not a display name - the admin
    # panel showed the raw ObjectId with no way to tell who created what.
    # Batch-resolve for this page only, not one query per invoice.
    # Some invoices (the anonymous/no-login flow) store a sentinel like
    # 'anonymous' rather than a real user id - filtering id__in on that
    # raises a mongoengine ValidationError, not just a harmless miss.
    owner_ids = {inv.created_by for inv in invoices
                 if inv.created_by and re.fullmatch(r'[0-9a-fA-F]{24}', inv.created_by)}
    owners = {str(u.id): (u.username or u.email) for u in User.objects.filter(id__in=list(owner_ids))}
    data     = [{
        'id':         str(inv.id),
        'number':     inv.invoice_number,
        'customer':   inv.customer_name,
        'amount':     float(inv.grand_total),
        'status':     inv.status,
        'template':   inv.template_style,
        'created_by': owners.get(inv.created_by, inv.created_by),
        'created_at': inv.created_at.strftime('%Y-%m-%d') if inv.created_at else '',
    } for inv in invoices]
    return JsonResponse({'invoices': data, 'total': total,
                         'pages': max(1, (total + per_page - 1) // per_page), 'page': page})


@admin_required
def admin_pricing_api(request):
    from django.conf import settings
    if request.method == 'GET':
        return JsonResponse({'plan_limits': settings.PLAN_LIMITS})

    if request.method == 'POST':
        body  = json.loads(request.body)
        plan  = body.get('plan')
        key   = body.get('key')
        value = body.get('value')
        if plan not in settings.PLAN_LIMITS:
            return JsonResponse({'error': 'Invalid plan'}, status=400)
        settings.PLAN_LIMITS[plan][key] = value
        AuditLog.log(request.admin_user, 'pricing_change',
                     f'{plan}.{key} → {value}', _get_ip(request))
        return JsonResponse({'ok': True})

admin_pricing_api = csrf_exempt(admin_pricing_api)


@csrf_exempt
@admin_required
def admin_templates_api(request):
    from apps.invoices import template_registry

    if request.method == 'GET':
        templates = []
        for t in template_registry.templates_payload():
            tid = t['id']
            usage = Invoice.objects(template_style=tid, status__ne='Cancelled').count()
            templates.append({
                'id': tid,
                'name': t['name'],
                'desc': t['desc'],
                'min_plan': t['effective_min_plan'],
                'default_min_plan': t['min_plan'],
                'blocked': t['blocked'],
                'usage_count': usage,
            })
        return JsonResponse({'templates': templates})

    body        = json.loads(request.body)
    template_id = body.get('template_id')
    action      = body.get('action')
    if template_id not in template_registry.TEMPLATE_IDS:
        return JsonResponse({'error': 'Invalid template'}, status=400)

    if action == 'block':
        if not TemplateBlock.objects(template_id=template_id).first():
            TemplateBlock(template_id=template_id,
                          blocked_by=str(request.admin_user.id)).save()
        AuditLog.log(request.admin_user, 'template_block', f'Blocked: {template_id}', _get_ip(request))
    elif action == 'unblock':
        TemplateBlock.objects(template_id=template_id).delete()
        AuditLog.log(request.admin_user, 'template_unblock', f'Unblocked: {template_id}', _get_ip(request))
    elif action == 'set_plan':
        min_plan = body.get('min_plan')
        valid_plans = ('free', 'plus', 'pro', 'unlimited')
        if min_plan not in valid_plans:
            return JsonResponse({'error': f'min_plan must be one of: {", ".join(valid_plans)}'}, status=400)
        cfg = TemplateConfig.objects(template_id=template_id).first()
        if not cfg:
            cfg = TemplateConfig(template_id=template_id)
        old_plan, cfg.min_plan = cfg.min_plan or 'free', min_plan
        cfg.updated_by = str(request.admin_user.id)
        cfg.save()
        AuditLog.log(request.admin_user, 'template_plan_change',
                     f'{template_id}: {old_plan} → {min_plan}', _get_ip(request))
        return JsonResponse({'ok': True, 'min_plan': cfg.min_plan})
    else:
        return JsonResponse({'error': 'Unknown action'}, status=400)
    return JsonResponse({'ok': True})
