from django.shortcuts import render, redirect


def pricing_page(request):
    return redirect('/#pricing')


def upgrade_page(request):
    return render(request, 'subscriptions/upgrade.html')


def subscription_manage_page(request):
    return render(request, 'subscriptions/manage.html')
