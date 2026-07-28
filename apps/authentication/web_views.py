from django.shortcuts import render, redirect


def landing_page(request):
    return render(request, 'landing.html')


def login_page(request):
    return render(request, 'auth/login.html')


def register_page(request):
    return render(request, 'auth/register.html')


def logout_page(request):
    return render(request, 'auth/logout.html')


def profile_page(request):
    return render(request, 'auth/profile.html')


def user_management_page(request):
    return render(request, 'auth/users.html')
