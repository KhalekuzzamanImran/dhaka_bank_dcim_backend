from pathlib import Path
from datetime import timedelta
from decouple import config
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent.parent
SECRET_KEY = config('SECRET_KEY', default='unsafe-dev-key-change-in-production')
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = [h.strip() for h in config('ALLOWED_HOSTS', default='localhost,127.0.0.1').split(',') if h.strip()]

DJANGO_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
]
THIRD_PARTY_APPS = [
    'rest_framework', 'rest_framework_simplejwt', 'django_filters', 'corsheaders', 'drf_spectacular',
]
LOCAL_APPS = [
    'apps.common', 'apps.accounts', 'apps.organizations', 'apps.access_control', 'apps.datacenters',
    'apps.devices', 'apps.telemetry', 'apps.alerts', 'apps.maintenance', 'apps.dashboards',
    'apps.reports', 'apps.notifications', 'apps.audit', 'collectors.snmp_collector',
    'collectors.modbus_collector', 'collectors.snmp_trap_receiver', 'apps.traps', 'collectors.scheduler',
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.common.middleware.RequestContextMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates',
    'DIRS': [BASE_DIR / 'templates'],
    'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.debug', 'django.template.context_processors.request',
        'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages',
    ]},
}]
WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default='postgresql://dcim:dcim@localhost:5432/dcim'),
        conn_max_age=600,
        conn_health_checks=True,
    )
}
AUTH_USER_MODEL = 'accounts.User'

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ('rest_framework_simplejwt.authentication.JWTAuthentication',),
    'DEFAULT_PERMISSION_CLASSES': ('apps.common.permissions.DCIMRBACPermission',),
    'DEFAULT_FILTER_BACKENDS': ('django_filters.rest_framework.DjangoFilterBackend','rest_framework.filters.SearchFilter','rest_framework.filters.OrderingFilter'),
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 50,
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': ['rest_framework.throttling.UserRateThrottle', 'rest_framework.throttling.AnonRateThrottle'],
    'DEFAULT_THROTTLE_RATES': {'user': config('API_USER_THROTTLE', default='2000/hour'), 'anon': config('API_ANON_THROTTLE', default='100/hour')},
}
SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=config('JWT_ACCESS_MINUTES', default=15, cast=int)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=config('JWT_REFRESH_DAYS', default=1, cast=int)),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'UPDATE_LAST_LOGIN': True,
}
SPECTACULAR_SETTINGS = {
    'TITLE': 'Bank DCIM Backend API',
    'DESCRIPTION': 'Production-grade DCIM backend API for bank data center infrastructure management.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
}
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Dhaka'
USE_I18N = True
USE_TZ = True
STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = 'media/'
MEDIA_ROOT = BASE_DIR / 'media'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
CORS_ALLOWED_ORIGINS = [o.strip() for o in config('CORS_ALLOWED_ORIGINS', default='http://localhost:3000,http://localhost:5173').split(',') if o.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in config('CSRF_TRUSTED_ORIGINS', default='http://localhost:3000,http://localhost:5173').split(',') if o.strip()]
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://localhost:6379/1')
CACHES = {'default': {'BACKEND': 'django_redis.cache.RedisCache', 'LOCATION': config('REDIS_CACHE_URL', default='redis://localhost:6379/2'), 'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'}}}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
X_FRAME_OPTIONS = 'DENY'

LOGGING = {
    'version': 1, 'disable_existing_loggers': False,
    'formatters': {'jsonlike': {'format': '%(asctime)s %(levelname)s %(name)s %(message)s'}},
    'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'jsonlike'}},
    'root': {'handlers': ['console'], 'level': config('LOG_LEVEL', default='INFO')},
}



# Production polling, Modbus, and SNMP trap configuration
SNMP_CELERY_QUEUE = config('SNMP_CELERY_QUEUE', default='snmp_normal')
MODBUS_CELERY_QUEUE = config('MODBUS_CELERY_QUEUE', default='modbus_normal')
POLLING_SCHEDULER_INTERVAL_SECONDS = config('POLLING_SCHEDULER_INTERVAL_SECONDS', default=5, cast=int)
POLLING_SCHEDULER_LIMIT = config('POLLING_SCHEDULER_LIMIT', default=200, cast=int)
SNMP_TRAP_LISTEN_HOST = config('SNMP_TRAP_LISTEN_HOST', default='0.0.0.0')
SNMP_TRAP_LISTEN_PORT = config('SNMP_TRAP_LISTEN_PORT', default=1162, cast=int)

CELERY_TASK_ROUTES = {
    'collectors.scheduler.tasks.enqueue_due_polls': {'queue': 'scheduler'},
    'collectors.snmp_collector.tasks.poll_snmp_device_task': {'queue': SNMP_CELERY_QUEUE},
    'collectors.snmp_collector.tasks.enqueue_due_snmp_polls': {'queue': 'scheduler'},
    'collectors.modbus_collector.tasks.poll_modbus_device_task': {'queue': MODBUS_CELERY_QUEUE},
    'collectors.modbus_collector.tasks.enqueue_due_modbus_polls': {'queue': 'scheduler'},
    'collectors.snmp_trap_receiver.tasks.process_snmp_trap_task': {'queue': 'traps'},
    'apps.alerts.tasks.*': {'queue': 'alerts'},
    'apps.reports.tasks.*': {'queue': 'reports'},
}

CELERY_BEAT_SCHEDULE = {
    'enqueue-due-polls-every-5-seconds': {
        'task': 'collectors.scheduler.tasks.enqueue_due_polls',
        'schedule': POLLING_SCHEDULER_INTERVAL_SECONDS,
        'kwargs': {'limit': POLLING_SCHEDULER_LIMIT},
    }
}
