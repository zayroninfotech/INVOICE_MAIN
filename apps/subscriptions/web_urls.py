from django.urls import path
from .web_views import pricing_page, upgrade_page, subscription_manage_page

urlpatterns = [
    path('', pricing_page, name='pricing'),
    path('upgrade/', upgrade_page, name='upgrade'),
    path('manage/', subscription_manage_page, name='subscription_manage'),
]
