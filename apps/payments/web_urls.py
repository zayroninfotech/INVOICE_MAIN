from django.urls import path
from . import web_views

urlpatterns = [
    path('', web_views.payment_list, name='payment_list_page'),
]
