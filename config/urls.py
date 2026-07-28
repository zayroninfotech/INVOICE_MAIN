from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from apps.authentication.web_views import landing_page

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
    path('home/', landing_page, name='landing'),
    path('', include('apps.dashboard.web_urls')),
    path('customers/', include('apps.customers.web_urls')),
    path('products/', include('apps.products.web_urls')),
    path('invoices/', include('apps.invoices.web_urls')),
    path('payments/', include('apps.payments.web_urls')),
    path('reports/', include('apps.reports.web_urls')),
    path('auth/', include('apps.authentication.web_urls')),
    path('pricing/', include('apps.subscriptions.web_urls')),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
