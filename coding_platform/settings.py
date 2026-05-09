import os
import environ
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False))

# ── Env File Selection ────────────────────────────────────────
if os.environ.get('RUNNING_IN_DOCKER'):
    environ.Env.read_env(str(BASE_DIR / '.env.prod'))
else:
    environ.Env.read_env(str(BASE_DIR / '.env'))

# ── Core ──────────────────────────────────────────────────────
SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', default=False)

ALLOWED_HOSTS = [
    "battlecodearena.gispilibhit.com",
    "www.battlecodearena.gispilibhit.com",
    "127.0.0.1",
    "localhost",
    "web",
]

# ── CSRF ──────────────────────────────────────────────────────
CSRF_TRUSTED_ORIGINS = [
    "http://battlecodearena.gispilibhit.com",
    "https://battlecodearena.gispilibhit.com",
    "http://www.battlecodearena.gispilibhit.com",
    "https://www.battlecodearena.gispilibhit.com",
]

# ── Apps ──────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'whitenoise.runserver_nostatic',
    'django.contrib.staticfiles',

    'channels',
    'django_celery_beat',

    'apps.accounts',
    'apps.contests',
    'apps.submissions',
    'apps.leaderboard',
]

# ── Middleware ────────────────────────────────────────────────
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

ROOT_URLCONF = 'coding_platform.urls'

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

WSGI_APPLICATION = 'coding_platform.wsgi.application'
ASGI_APPLICATION = 'coding_platform.asgi.application'

# ── Database ──────────────────────────────────────────────────
DATABASES = {
    'default': env.db('DATABASE_URL')
}
CONN_MAX_AGE = 60

# ── Redis ─────────────────────────────────────────────────────
REDIS_URL = env('REDIS_URL', default='redis://redis:6379/0')

# ── Channels ──────────────────────────────────────────────────
if os.environ.get('RUNNING_IN_DOCKER'):
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [REDIS_URL],
            },
        }
    }
else:
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        }
    }

# ── Cache ─────────────────────────────────────────────────────
if os.environ.get('RUNNING_IN_DOCKER'):
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
        }
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        }
    }
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# ── Static & Media ────────────────────────────────────────────
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'

STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# ── AUTH ──────────────────────────────────────────────────────
AUTH_USER_MODEL = 'accounts.User'
LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/contests/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# ── PROXY FIX ────────────────────────────────────────────────
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ── 🔥 CELERY (FIXED & STABLE) ────────────────────────────────
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL

CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = 'Asia/Kolkata'

CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True

# ── Production Security ───────────────────────────────────────
if not DEBUG:
    SECURE_SSL_REDIRECT = False

    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_SECURE = False

    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True

    SESSION_COOKIE_SAMESITE = 'Lax'
    CSRF_COOKIE_SAMESITE = 'Lax'

    X_FRAME_OPTIONS = 'DENY'

# ── TIME ───────────────────────────────────────────────────────
TIME_ZONE = 'Asia/Kolkata'
USE_TZ = True

# ── Code Executor ─────────────────────────────────────────────
USE_DOCKER_EXECUTOR = os.environ.get('RUNNING_IN_DOCKER', False)
EXECUTOR_IMAGE = 'python:3.11-alpine'
EXECUTOR_JAVA_IMAGE = 'openjdk:17-alpine'
EXECUTOR_TIMEOUT = 30
EXECUTOR_MEMORY_LIMIT = '128m'
EXECUTOR_CPU_QUOTA = 50000

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'