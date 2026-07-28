from django.urls import path
from . import web_views

urlpatterns = [
    path('', web_views.dashboard, name='dashboard'),
]
