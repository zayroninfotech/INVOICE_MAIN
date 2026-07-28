from django.urls import path
from . import web_views

urlpatterns = [
    path('', web_views.customer_list, name='customer_list_page'),
    path('add/', web_views.customer_add, name='customer_add_page'),
    path('<str:pk>/edit/', web_views.customer_edit, name='customer_edit_page'),
]
