import os
from pathlib import Path
from datetime import timedelta
from dotenv import load_dotenv

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
ALLOWED_HOSTS = env('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
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
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
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
                'django.contrib.auth.context_processors.auth',
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

MONGODB_URI_PRIMARY = env('MONGODB_URI_PRIMARY', 'mongodb://zayronadmin:Zayroninfo%402026@187.127.131.93:27018/?authSource=admin')
MONGODB_URI_FALLBACK = env('MONGODB_URI_FALLBACK', 'mongodb://localhost:27017')
MONGODB_DB = env('MONGODB_DB', 'invoice_db')

def _try_connect(uri, db):
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command('ping')
        mongoengine.connect(host=uri, db=db, alias='default')
        return True
    except Exception:
        return False

if _try_connect(MONGODB_URI_PRIMARY, MONGODB_DB):
    MONGO_URI = MONGODB_URI_PRIMARY
    print(f"[MongoDB] Connected to PRIMARY ({MONGODB_URI_PRIMARY[:30]}...)")
else:
    _try_connect(MONGODB_URI_FALLBACK, MONGODB_DB)
    MONGO_URI = MONGODB_URI_FALLBACK
    print("[MongoDB] PRIMARY unreachable — using FALLBACK (localhost)")

# Django default DB (SQLite) only for Django admin/sessions/auth tables
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
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

# Celery
CELERY_BROKER_URL = env('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
