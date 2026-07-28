from django.shortcuts import render


def reports_page(request):
    return render(request, 'reports/index.html')
