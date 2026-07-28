from django.urls import path
from .views import (RegisterView, LoginView, RefreshTokenView,
                    MeView, ChangePasswordView, UserListView, UserDetailView,
                    BusinessProfileView, BusinessProfileLogoView)

urlpatterns = [
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('refresh/', RefreshTokenView.as_view(), name='token_refresh'),
    path('me/', MeView.as_view(), name='me'),
    path('change-password/', ChangePasswordView.as_view(), name='change_password'),
    # Business profile
    path('business-profile/', BusinessProfileView.as_view(), name='business_profile'),
    path('business-profile/logo/', BusinessProfileLogoView.as_view(), name='business_logo'),
    # Superadmin: user management
    path('users/', UserListView.as_view(), name='user_list'),
    path('users/<str:pk>/', UserDetailView.as_view(), name='user_detail'),
]
