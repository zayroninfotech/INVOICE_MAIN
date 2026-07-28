from django.urls import path
from . import web_views

urlpatterns = [
    path('', web_views.reports_page, name='reports_page'),
]
