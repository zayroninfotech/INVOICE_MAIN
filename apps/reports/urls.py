from django.urls import path
from .views import SalesReportView, SalesReportCSVView, MonthlyRevenueView

urlpatterns = [
    path('sales/', SalesReportView.as_view(), name='sales_report'),
    path('sales/csv/', SalesReportCSVView.as_view(), name='sales_report_csv'),
    path('monthly-revenue/', MonthlyRevenueView.as_view(), name='monthly_revenue'),
]
