from django.urls import path
from .views import CustomerListCreateView, CustomerDetailView

urlpatterns = [
    path('', CustomerListCreateView.as_view(), name='customer_list'),
    path('<str:pk>/', CustomerDetailView.as_view(), name='customer_detail'),
]
