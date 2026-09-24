from django.urls import path
from .views import (InvoiceListCreateView, InvoiceDetailView,
                    InvoicePDFView, InvoiceEmailView, InvoiceStatusView,
                    AvailableTemplatesView, PublicInvoiceView,
                    PublicInvoicePDFView, InvoiceSummaryView, InvoiceExportView)
from .free_views import FreeInvoiceView, FreeInvoicePDFView

urlpatterns = [
    path('free/', FreeInvoiceView.as_view(), name='free_invoice'),
    # Public (token-authorised) — must stay above '<str:pk>/'.
    path('public/<str:token>/', PublicInvoiceView.as_view(), name='invoice_public'),
    path('public/<str:token>/pdf/', PublicInvoicePDFView.as_view(), name='invoice_public_pdf'),
    path('free/<str:pk>/pdf/', FreeInvoicePDFView.as_view(), name='free_invoice_pdf'),
    path('templates/', AvailableTemplatesView.as_view(), name='invoice_templates'),
    path('summary/', InvoiceSummaryView.as_view(), name='invoice_summary'),
    path('export/', InvoiceExportView.as_view(), name='invoice_export'),
    path('', InvoiceListCreateView.as_view(), name='invoice_list'),
    path('<str:pk>/', InvoiceDetailView.as_view(), name='invoice_detail'),
    path('<str:pk>/pdf/', InvoicePDFView.as_view(), name='invoice_pdf'),
    path('<str:pk>/email/', InvoiceEmailView.as_view(), name='invoice_email'),
    path('<str:pk>/status/', InvoiceStatusView.as_view(), name='invoice_status'),
]
