"""Django settings for the CRM portal (env-driven; see .env.example)."""

import dj_database_url
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from typing import Optional

from django.core.exceptions import ImproperlyConfigured

# Load environment variables from .env file for local/dev.
# On production (e.g. Hostinger VPS), prefer real environment variables
# and use a minimal .env only if needed.
load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


def get_bool_env(name: str, default: bool = False) -> bool:
    """Parse a boolean environment variable in a robust way."""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# Render/build pipelines often run collectstatic before runtime env is fully applied.
# Allow a one-off dummy key only for known safe management commands.
_DUMMY_BUILD_SECRET = "django-insecure-build-only-not-for-production"


def _running_management_command(*names: str) -> bool:
    return len(sys.argv) >= 2 and sys.argv[1] in names


def _secret_key_from_env() -> Optional[str]:
    """First non-empty SECRET_KEY or DJANGO_SECRET_KEY (Render/docs use both names)."""
    for name in ("SECRET_KEY", "DJANGO_SECRET_KEY"):
        val = os.environ.get(name)
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


SECRET_KEY = _secret_key_from_env()
if not SECRET_KEY:
    if _running_management_command(
        "collectstatic", "migrate", "makemigrations", "check"
    ):
        SECRET_KEY = _DUMMY_BUILD_SECRET
    else:
        raise ImproperlyConfigured(
            "Set a non-empty SECRET_KEY (or DJANGO_SECRET_KEY). "
            "On Render with render.yaml: Environment Groups → open 'crm-secrets' → add SECRET_KEY, "
            "or add SECRET_KEY under your web service → Environment."
        )

# DJANGO_DEBUG=True  -> DEBUG = True  (local/dev)
# DJANGO_DEBUG=False -> DEBUG = False (production)
DEBUG = get_bool_env('DJANGO_DEBUG', default=True)

if (
    not DEBUG
    and SECRET_KEY == _DUMMY_BUILD_SECRET
    and not _running_management_command(
        "collectstatic", "migrate", "makemigrations", "check"
    )
):
    raise ImproperlyConfigured(
        "SECRET_KEY environment variable is required when DJANGO_DEBUG=False."
    )

def _normalize_allowed_host_entry(entry: str) -> str:
    """Strip whitespace, trailing slashes, and accidental paths (ALLOWED_HOSTS = hostname only)."""
    s = (entry or "").strip()
    if not s:
        return ""
    s = s.split("/")[0].strip()
    return s


def _append_unique(items: list, value: str) -> None:
    v = (value or "").strip()
    if v and v not in items:
        items.append(v)


_raw_allowed_hosts = (
    os.environ.get('DJANGO_ALLOWED_HOSTS')
    or os.environ.get('ALLOWED_HOSTS')
    or '127.0.0.1,localhost'
)
ALLOWED_HOSTS = [
    h
    for h in (
        _normalize_allowed_host_entry(x) for x in _raw_allowed_hosts.split(',')
    )
    if h
]

# Trusted origins for CSRF on HTTPS (Django 4+). e.g. https://crm.example.com,https://www.example.com
_csrf_origins = os.environ.get('CSRF_TRUSTED_ORIGINS', '').strip()
CSRF_TRUSTED_ORIGINS = [
    o.strip().rstrip("/") for o in _csrf_origins.split(',') if o.strip()
]

# --- Vercel: preview URLs differ per deploy (e.g. *-hash-team.vercel.app). VERCEL_URL is set automatically.
# https://vercel.com/docs/projects/environment-variables/system-environment-variables
if os.environ.get("VERCEL"):
    _append_unique(ALLOWED_HOSTS, ".vercel.app")

for _vu in (
    os.environ.get("VERCEL_URL", "").strip(),
    os.environ.get("VERCEL_BRANCH_URL", "").strip(),
):
    if _vu:
        _host = _normalize_allowed_host_entry(_vu.split("://")[-1])
        if _host:
            _append_unique(ALLOWED_HOSTS, _host)
            _append_unique(CSRF_TRUSTED_ORIGINS, f"https://{_host}")

_vprod = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL", "").strip()
if _vprod:
    _vp = _vprod if "://" in _vprod else f"https://{_vprod}"
    try:
        _parts = urlparse(_vp)
        if _parts.hostname:
            _append_unique(ALLOWED_HOSTS, _parts.hostname)
        if _parts.scheme in ("http", "https") and _parts.netloc:
            _append_unique(
                CSRF_TRUSTED_ORIGINS,
                f"{_parts.scheme}://{_parts.netloc}".rstrip("/"),
            )
    except ValueError:
        pass

# Railway / Render (optional)
_railway_public = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
if _railway_public:
    _append_unique(ALLOWED_HOSTS, _railway_public)
    _append_unique(CSRF_TRUSTED_ORIGINS, f"https://{_railway_public}")

_render_external = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
if _render_external:
    try:
        _rp = urlparse(_render_external)
        if _rp.hostname:
            _append_unique(ALLOWED_HOSTS, _rp.hostname)
        if _rp.scheme in ("http", "https") and _rp.netloc:
            _append_unique(
                CSRF_TRUSTED_ORIGINS,
                f"{_rp.scheme}://{_rp.netloc}".rstrip("/"),
            )
    except ValueError:
        pass

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'

# Security Headers for Production
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # Behind Nginx with HTTPS termination; redirect HTTP to HTTPS
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = 'DENY'


INSTALLED_APPS = [
    # Django Apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # My Apps
    'main_app.apps.MainAppConfig'
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',

    # My Middleware
    'main_app.middleware.LoginCheckMiddleware',
]

# Enable WhiteNoise only in production so that local development
# does not require the STATIC_ROOT directory and avoids noisy warnings.
if not DEBUG:
    MIDDLEWARE.insert(
        MIDDLEWARE.index('django.middleware.security.SecurityMiddleware') + 1,
        'whitenoise.middleware.WhiteNoiseMiddleware',
    )

ROOT_URLCONF = 'college_management_system.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': ['main_app/templates'], #My App Templates
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'main_app.context_processors.notification_count',
                'main_app.context_processors.lead_status_info',
                'main_app.context_processors.pending_task_count',
                'main_app.context_processors.admin_permissions',
            ],
        },
    },
]

WSGI_APPLICATION = 'college_management_system.wsgi.application'


# Database configuration
#
# Production / Supabase: set DATABASE_URL to the Postgres URI from
# Supabase Dashboard → Project Settings → Database (use "URI" or "Session pooler").
# Example: postgresql://postgres.[ref]:PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres?sslmode=require
#
# Set USE_SQLITE_LOCAL=True in .env for local dev without Postgres (ignores DATABASE_URL when DEBUG).
_db_url = os.environ.get('DATABASE_URL') or f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
if DEBUG and os.environ.get('USE_SQLITE_LOCAL', '').strip().lower() in ('1', 'true', 'yes'):
    _db_url = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"
DATABASES = {'default': dj_database_url.parse(_db_url)}
_db = DATABASES['default']
_engine = str(_db.get('ENGINE', ''))

if 'postgresql' in _engine:
    # Supabase requires TLS; pooler URIs usually include ?sslmode=require — ensure it is set.
    _db.setdefault('OPTIONS', {})
    _db['OPTIONS'].setdefault('sslmode', 'require')
    try:
        _pg_port = int(str(_db.get('PORT') or 5432))
    except ValueError:
        _pg_port = 5432
    _pg_host_l = str(_db.get('HOST', '')).lower()
    _is_supabase_pooler = 'pooler.supabase.com' in _pg_host_l

    # Transaction pooler (6543): required — connections cannot stay open across requests.
    if _pg_port == 6543 or get_bool_env('SUPABASE_TRANSACTION_POOLER', False):
        _db['CONN_MAX_AGE'] = 0
    elif _is_supabase_pooler and _pg_port == 5432:
        # Session pooler: tiny max-clients limit. Persistent CONN_MAX_AGE (e.g. 600) + many
        # Vercel/serverless instances or Gunicorn workers → "MaxClientsInSessionMode".
        # Default 0; set DATABASE_CONN_MAX_AGE only if you know your pool size (dedicated VPS).
        _db['CONN_MAX_AGE'] = int(os.environ.get('DATABASE_CONN_MAX_AGE', '0'))
    else:
        _db['CONN_MAX_AGE'] = int(os.environ.get('DATABASE_CONN_MAX_AGE', '600'))

    # Direct db.*.supabase.co often resolves to IPv6 first; Vercel/Railway/Docker may fail with
    # "Cannot assign requested address". Use IPv4 for the TCP connection but keep HOST for TLS name.
    _prefer_ipv4 = (
        get_bool_env('DATABASE_PREFER_IPV4', False)
        or get_bool_env('SUPABASE_PREFER_IPV4', False)
        or bool(os.environ.get('VERCEL'))
        or bool(os.environ.get('RAILWAY_ENVIRONMENT'))
    )
    if _prefer_ipv4 and 'hostaddr' not in _db['OPTIONS']:
        _pg_host = _db.get('HOST')
        if _pg_host:
            try:
                _ipv4_infos = socket.getaddrinfo(
                    _pg_host, None, socket.AF_INET, socket.SOCK_STREAM
                )
                if _ipv4_infos:
                    _db['OPTIONS']['hostaddr'] = _ipv4_infos[0][4][0]
            except OSError:
                pass
else:
    _db['CONN_MAX_AGE'] = int(os.environ.get('DATABASE_CONN_MAX_AGE', '600'))

if DEBUG:
    db_url = DATABASES['default'].get('NAME', '')
    if 'sqlite' in str(db_url).lower() or 'sqlite' in str(DATABASES['default'].get('ENGINE', '')).lower():
        # SQLite-specific optimizations will be applied via connection signals
        pass


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


LANGUAGE_CODE = 'en-us'

# TIME_ZONE = 'UTC'  # Commented out to avoid conflict

USE_I18N = True

USE_L10N = True

USE_TZ = True


STATIC_URL = '/static/'

MEDIA_URL = '/media/'

# In production on Hostinger VPS, Nginx is configured to serve static files
# from BASE_DIR / "staticfiles". Keep this in sync with nginx_config.conf.
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
AUTH_USER_MODEL = 'main_app.CustomUser'
AUTHENTICATION_BACKENDS = ['main_app.EmailBackend.EmailBackend']
TIME_ZONE = 'Asia/Kolkata'  # Indian Standard Time (IST)

# EMAIL_BACKEND = 'django.core.mail.backends.filebased.EmailBackend'
# EMAIL_FILE_PATH = os.path.join(BASE_DIR, "sent_mails")

EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_HOST_USER = os.environ.get('EMAIL_ADDRESS')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_PASSWORD')
EMAIL_USE_TLS = True
DEFAULT_FROM_EMAIL = f"CRM Portal <{os.environ.get('EMAIL_ADDRESS')}>"

# Static files settings
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'main_app/static'),
]

# Allow bulk operations with many form fields (e.g. deleting many leads at once)
# Default is 1000; bump to a safer higher limit for admin actions.
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.environ.get('DATA_UPLOAD_MAX_NUMBER_FIELDS', '10000'))

# Static files storage for Whitenoise
if not DEBUG:
    # Use non-manifest storage to avoid build failures on vendor CSS assets
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'
    WHITENOISE_MANIFEST_STRICT = False
else:
    # Local development: use finder for faster reloading
    # No need to run collectstatic in development
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
    
# Static files finders for faster development
STATICFILES_FINDERS = [
    'django.contrib.staticfiles.finders.FileSystemFinder',
    'django.contrib.staticfiles.finders.AppDirectoriesFinder',
]



# Session Configuration
SESSION_COOKIE_AGE = 1800  # 30 minutes in seconds
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
# False avoids writing the session to the DB/cache on every request (major latency win on remote DB).
SESSION_SAVE_EVERY_REQUEST = get_bool_env('SESSION_SAVE_EVERY_REQUEST', default=False)

# Dashboard caches (seconds). Set 0 to disable counsellor snapshot cache. Admin cache uses min 1 if enabled.
ADMIN_DASHBOARD_CACHE_SECONDS = int(os.environ.get('ADMIN_DASHBOARD_CACHE_SECONDS', '45'))
COUNSELLOR_SNAPSHOT_CACHE_SECONDS = int(os.environ.get('COUNSELLOR_SNAPSHOT_CACHE_SECONDS', '45'))

# Upload limits (import + general uploads)
MAX_LEAD_IMPORT_MB = int(os.environ.get('MAX_LEAD_IMPORT_MB', '10'))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get('DATA_UPLOAD_MAX_MEMORY_SIZE', str(12 * 1024 * 1024)))  # 12 MiB default
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get('FILE_UPLOAD_MAX_MEMORY_SIZE', str(5 * 1024 * 1024)))  # 5 MiB to disk after

# AI/LLM Settings
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

# Firebase settings
FIREBASE_CONFIG = {
    "apiKey": os.environ.get("FIREBASE_API_KEY"),
    "authDomain": os.environ.get("FIREBASE_AUTH_DOMAIN"),
    "databaseURL": os.environ.get("FIREBASE_DATABASE_URL"),
    "projectId": os.environ.get("FIREBASE_PROJECT_ID"),
    "storageBucket": os.environ.get("FIREBASE_STORAGE_BUCKET"),
    "messagingSenderId": os.environ.get("FIREBASE_MESSAGING_SENDER_ID"),
    "appId": os.environ.get("FIREBASE_APP_ID"),
    "measurementId": os.environ.get("FIREBASE_MEASUREMENT_ID"),
}
if not all(FIREBASE_CONFIG.values()):
    FIREBASE_CONFIG = None

# Caching
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    }
}

# If Redis is available, use it for caching and celery
REDIS_URL = os.environ.get('REDIS_URL')
if REDIS_URL:
    CACHES['default'] = {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': REDIS_URL,
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        }
    }
    
# Celery Configuration
CELERY_BROKER_URL = REDIS_URL if REDIS_URL else "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = REDIS_URL if REDIS_URL else "redis://localhost:6379/0"
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE


# Logging configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}



