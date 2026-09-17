from django.shortcuts import render, redirect


def _marketing_nav(active):
    """Nav model for the shared site navbar — views pass this to templates."""
    links = [
        {'label': 'Home',    'href': '/',          'key': 'home'},
        {'label': 'About',   'href': '/about/',    'key': 'about'},
        {'label': 'Support', 'href': '/support/',  'key': 'support'},
    ]
    for l in links:
        l['active'] = l['key'] == active
    return links


def landing_page(request):
    # About/Support live in the footer — header keeps only in-page anchors.
    ctx = {
        'nav_links': [
            {'label': 'How it works', 'href': '#how-it-works', 'active': False},
            {'label': 'Features',     'href': '#features',     'active': False},
            {'label': 'FAQ',          'href': '#faq',          'active': False},
        ],
    }
    response = render(request, 'landing.html', ctx)
    response['Cache-Control'] = 'no-cache, must-revalidate'
    return response


def login_page(request):
    return redirect('/?login')


def register_page(request):
    return redirect('/?register')


def logout_page(request):
    return render(request, 'auth/logout.html')


def profile_page(request):
    return render(request, 'auth/profile.html')


def user_management_page(request):
    return render(request, 'auth/users.html')


def about_page(request):
    return render(request, 'about.html', {'nav_links': _marketing_nav('about')})


def support_page(request):
    ctx = {'nav_links': _marketing_nav('support'), 'auth_user': None, 'subscription': None}
    uid = request.session.get('user_id')
    if uid:
        try:
            from apps.authentication.models import User
            from apps.subscriptions.models import Subscription
            user = User.objects.get(id=uid)
            sub  = Subscription.objects.filter(user_id=str(user.id)).first()
            ctx['auth_user']    = user
            ctx['subscription'] = sub
            ctx['nav_cta'] = {'href': '/dashboard/', 'label': 'Dashboard'}
        except Exception:
            pass
    return render(request, 'support.html', ctx)
