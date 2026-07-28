from django.shortcuts import render


def pricing_page(request):
    return render(request, 'subscriptions/pricing.html')


def upgrade_page(request):
    return render(request, 'subscriptions/upgrade.html')


def subscription_manage_page(request):
    return render(request, 'subscriptions/manage.html')
