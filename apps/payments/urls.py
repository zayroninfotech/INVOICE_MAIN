from django.urls import path
from .views import PaymentListCreateView, PaymentDetailView

urlpatterns = [
    path('', PaymentListCreateView.as_view(), name='payment_list'),
    path('<str:pk>/', PaymentDetailView.as_view(), name='payment_detail'),
]
