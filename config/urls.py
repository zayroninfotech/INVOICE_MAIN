from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.authentication.web_views import landing_page, about_page, support_page
from apps.authentication import admin_views
from apps.invoices import web_views as invoice_web_views

urlpatterns = [
    path('admin/', admin.site.urls),

    # API routes
    path('api/auth/', include('apps.authentication.urls')),
    path('api/customers/', include('apps.customers.urls')),
    path('api/products/', include('apps.products.urls')),
    path('api/invoices/', include('apps.invoices.urls')),
    path('api/payments/', include('apps.payments.urls')),
    path('api/reports/', include('apps.reports.urls')),
    path('api/dashboard/', include('apps.dashboard.urls')),
    path('api/subscriptions/', include('apps.subscriptions.urls')),

    # Frontend views
    path('', landing_page, name='landing'),
    path('about/', about_page, name='about'),
    # Short public link emailed to customers — kept top-level and terse so it
    # survives being copied out of an email client.
    path('i/<str:token>/', invoice_web_views.invoice_approve, name='invoice_approve_page'),
    path('support/', support_page, name='support'),
    path('dashboard/', include('apps.dashboard.web_urls')),
    path('customers/', include('apps.customers.web_urls')),
    path('products/', include('apps.products.web_urls')),
    path('invoices/', include('apps.invoices.web_urls')),
    path('payments/', include('apps.payments.web_urls')),
    path('reports/', include('apps.reports.web_urls')),
    path('auth/', include('apps.authentication.web_urls')),
    path('pricing/', include('apps.subscriptions.web_urls')),

    # Custom admin panel
    path('z-admin/',               admin_views.admin_dashboard,   name='admin_panel'),
    path('z-admin/sso/',           admin_views.admin_sso,         name='admin_sso'),
    path('z-admin/login/',         admin_views.admin_login,       name='admin_login'),
    path('z-admin/logout/',        admin_views.admin_logout,      name='admin_logout'),
    path('z-admin/api/stats/',     admin_views.admin_stats,       name='admin_stats'),
    path('z-admin/api/users/',     admin_views.admin_users_api,   name='admin_users_api'),
    path('z-admin/api/audit/',     admin_views.admin_audit_api,   name='admin_audit_api'),
    path('z-admin/api/invoices/',  admin_views.admin_invoices_api,name='admin_invoices_api'),
    path('z-admin/api/pricing/',   admin_views.admin_pricing_api, name='admin_pricing_api'),
    path('z-admin/api/templates/', admin_views.admin_templates_api,name='admin_templates_api'),
    path('z-admin/api/smtp/',      admin_views.admin_smtp_api,    name='admin_smtp_api'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
