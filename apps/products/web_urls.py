from django.urls import path
from . import web_views

urlpatterns = [
    path('', web_views.product_list, name='product_list_page'),
    path('add/', web_views.product_add, name='product_add_page'),
    path('<str:pk>/edit/', web_views.product_edit, name='product_edit_page'),
]
