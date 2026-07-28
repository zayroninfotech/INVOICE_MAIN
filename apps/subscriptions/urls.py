from django.urls import path
from .views import (
    MySubscriptionView, PricingConfigView,
    CreateOrderView, VerifyPaymentView,
    SuperadminSubscriptionsView, SuperadminChangePlanView, SuperadminPlanSettingsView,
)

urlpatterns = [
    path('my/', MySubscriptionView.as_view(), name='my_subscription'),
    path('pricing-config/', PricingConfigView.as_view(), name='pricing_config'),
    path('create-order/', CreateOrderView.as_view(), name='create_order'),
    path('verify-payment/', VerifyPaymentView.as_view(), name='verify_payment'),
    # Superadmin
    path('admin/all/', SuperadminSubscriptionsView.as_view(), name='admin_subscriptions'),
    path('admin/change-plan/<str:pk>/', SuperadminChangePlanView.as_view(), name='admin_change_plan'),
    path('admin/settings/', SuperadminPlanSettingsView.as_view(), name='admin_plan_settings'),
]
