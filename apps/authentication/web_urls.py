from django.urls import path
from . import web_views

urlpatterns = [
    path('login/', web_views.login_page, name='login_page'),
    path('register/', web_views.register_page, name='register_page'),
    path('logout/', web_views.logout_page, name='logout_page'),
    path('profile/', web_views.profile_page, name='profile_page'),
    path('users/', web_views.user_management_page, name='user_management_page'),
]
