from django.urls import path
from . import web_views

urlpatterns = [
    path('', web_views.invoice_list, name='invoice_list_page'),
    path('create/', web_views.invoice_create, name='invoice_create_page'),
    path('<str:pk>/', web_views.invoice_detail, name='invoice_detail_page'),
    path('<str:pk>/edit/', web_views.invoice_edit, name='invoice_edit_page'),
    # The page Chrome prints into the PDF — viewable for visual comparison.
    path('<str:pk>/print/', web_views.invoice_print, name='invoice_print_page'),
]
