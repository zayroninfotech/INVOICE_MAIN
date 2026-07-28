from django.urls import path
from .views import (InvoiceListCreateView, InvoiceDetailView,
                    InvoicePDFView, InvoiceEmailView, InvoiceStatusView)
from .free_views import FreeInvoiceView, FreeInvoicePDFView

urlpatterns = [
    path('free/', FreeInvoiceView.as_view(), name='free_invoice'),
    path('free/<str:pk>/pdf/', FreeInvoicePDFView.as_view(), name='free_invoice_pdf'),
    path('', InvoiceListCreateView.as_view(), name='invoice_list'),
    path('<str:pk>/', InvoiceDetailView.as_view(), name='invoice_detail'),
    path('<str:pk>/pdf/', InvoicePDFView.as_view(), name='invoice_pdf'),
    path('<str:pk>/email/', InvoiceEmailView.as_view(), name='invoice_email'),
    path('<str:pk>/status/', InvoiceStatusView.as_view(), name='invoice_status'),
]
