import os
import mimetypes
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('font/woff', '.woff')

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / '.env')


def env(key, default=None, cast=None):
    value = os.environ.get(key, default)
    if cast and value is not None:
        if cast is bool:
            return str(value).lower() in ('true', '1', 'yes')
        return cast(value)
    return value


SECRET_KEY = env('SECRET_KEY', 'django-insecure-change-this-in-production')
DEBUG = env('DEBUG', 'True', cast=bool)
ALLOWED_HOSTS = env('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') + ['*']

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'django_filters',
    # Apps
    'apps.authentication',
    'apps.customers',
    'apps.products',
    'apps.invoices',
    'apps.payments',
    'apps.reports',
    'apps.dashboard',
    'apps.subscriptions',
]

# ── Subscription Plan Limits ──────────────────────────────────────────────────
PLAN_LIMITS = {
    'anonymous': {'invoices_per_session': 100000, 'invoices_per_day': None, 'invoices_per_month': None,
                  'label': 'Anonymous', 'price': 0, 'price_display': '₹0', 'period': 'per session'},
    'free':      {'invoices_per_session': None, 'invoices_per_day': 5, 'invoices_per_month': None,
                  'label': 'Free', 'price': 0, 'price_display': '₹0', 'period': 'forever'},
    'plus':      {'invoices_per_session': None, 'invoices_per_day': None, 'invoices_per_month': 300,
                  'label': 'Plus', 'price': 199, 'price_display': '₹199', 'period': 'month'},
    'pro':       {'invoices_per_session': None, 'invoices_per_day': None, 'invoices_per_month': 1000,
                  'label': 'Pro', 'price': 499, 'price_display': '₹499', 'period': 'month'},
    'unlimited': {'invoices_per_session': None, 'invoices_per_day': None, 'invoices_per_month': None,
                  'label': 'Unlimited', 'price': 999, 'price_display': '₹999', 'period': 'month'},
}
PAID_PLANS = ('plus', 'pro', 'unlimited')

SESSION_ENGINE = 'utils.mongo_session'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# MongoDB via MongoEngine
import mongoengine
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

# Credentials live only in .env — never hard-code them here.
MONGODB_URI_PRIMARY = env('MONGODB_URI_PRIMARY', '')
MONGODB_URI_FALLBACK = env('MONGODB_URI_FALLBACK', 'mongodb://localhost:27017')
MONGODB_DB = env('MONGODB_DB', 'invoice_db')


def _mongo_host(uri):
    return uri.split('://', 1)[-1].rsplit('@', 1)[-1].split('/')[0] if uri else '(not set)'


def _try_connect(uri, db):
    if not uri:
        return False
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command('ping')
        mongoengine.connect(host=uri, db=db, alias='default')
        return True
    except Exception:
        return False


if _try_connect(MONGODB_URI_PRIMARY, MONGODB_DB):
    MONGO_URI = MONGODB_URI_PRIMARY
    print(f"[MongoDB] Connected to PRIMARY {_mongo_host(MONGODB_URI_PRIMARY)} / {MONGODB_DB}")
else:
    _try_connect(MONGODB_URI_FALLBACK, MONGODB_DB)
    MONGO_URI = MONGODB_URI_FALLBACK
    print(f"[MongoDB] PRIMARY {_mongo_host(MONGODB_URI_PRIMARY)} unreachable — using FALLBACK {_mongo_host(MONGODB_URI_FALLBACK)}")

# No relational DB — all data lives in MongoDB via MongoEngine.
# A dummy backend satisfies Django's internal checks without connecting to anything.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.dummy',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
WHITENOISE_MIMETYPES = {
    '.woff2': 'font/woff2',
    '.woff':  'font/woff',
    '.ttf':   'font/ttf',
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_FILTER_BACKENDS': (
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
}

# JWT
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=env('JWT_ACCESS_TOKEN_LIFETIME_MINUTES', 60, cast=int)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=env('JWT_REFRESH_TOKEN_LIFETIME_DAYS', 7, cast=int)),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'AUTH_HEADER_TYPES': ('Bearer',),
}

# Email
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = env('EMAIL_PORT', 587, cast=int)
EMAIL_USE_TLS = env('EMAIL_USE_TLS', 'True', cast=bool)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', 'Invoice System <noreply@example.com>')

# Public base URL used to build the customer approval link in emailed invoices.
# Left blank, the link is built from the request that triggered the send, which
# is right in dev but wrong behind a proxy that rewrites Host — set it in .env
# for any real deployment.
SITE_URL = env('SITE_URL', '')

# Celery
CELERY_BROKER_URL = env('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
