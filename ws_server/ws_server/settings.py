"""
Production-ready settings for Django + Channels (ASGI).

Key requirements implemented:
- Django + Django Channels (ASGI)
- RedisChannelLayer (NOT InMemoryChannelLayer)
- Environment-based configuration
- Works behind AWS ALB + Auto Scaling Group (no sticky sessions)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

from corsheaders.defaults import default_headers as _cors_default_headers

try:
    # Optional: allows local dev to load env vars from a `.env` file.
    # In production, prefer systemd EnvironmentFile or EC2 launch template env.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str | None = None) -> str | None:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _env_bool(name: str, default: bool = False) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_csv(name: str, default: str = "") -> List[str]:
    raw = _env(name, default) or ""
    return [x.strip() for x in raw.split(",") if x.strip()]


# SECURITY WARNING: Do not hardcode secrets in code.
DEBUG = _env_bool("DJANGO_DEBUG", default=False)

SECRET_KEY = _env("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")
if not DEBUG and (not SECRET_KEY or SECRET_KEY.startswith("dev-insecure-")):
    raise RuntimeError("DJANGO_SECRET_KEY must be set in production")

ALLOWED_HOSTS = _env_csv("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1")

# CORS: allow requests from frontend (e.g. React on localhost:3000)
CORS_ALLOWED_ORIGINS = _env_csv("CORS_ALLOWED_ORIGINS", default="http://localhost:3000,http://127.0.0.1:3000")
CORS_ALLOW_CREDENTIALS = True
# Allow X-API-KEY so preflight includes Access-Control-Allow-Headers: x-api-key
CORS_ALLOW_HEADERS = list(_cors_default_headers) + ["x-api-key"]

# CSRF: trust origins for cookie-based CSRF (e.g. frontend on localhost:3000)
CSRF_TRUSTED_ORIGINS = _env_csv("CSRF_TRUSTED_ORIGINS", default="http://localhost:3000,http://127.0.0.1:3000")

# When serving behind an ALB, Django must respect X-Forwarded-* headers.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Optional hardening toggles (recommended in production).
SESSION_COOKIE_SECURE = _env_bool("DJANGO_SESSION_COOKIE_SECURE", default=not DEBUG)
CSRF_COOKIE_SECURE = _env_bool("DJANGO_CSRF_COOKIE_SECURE", default=not DEBUG)
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)

# If you terminate TLS at the ALB, set HSTS at Django *only* if all traffic is HTTPS.
SECURE_HSTS_SECONDS = int(_env("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "0") or "0")
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)


INSTALLED_APPS = [
    "corsheaders",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Channels must be installed to enable ASGI + websocket routing.
    "channels",
    # Our websocket + redis ownership logic.
    "realtime.apps.RealtimeConfig",
]

# Order: CanonicalHost first (rewrite private-IP Host to ALB host), then API key auth,
# then Health/CORS so both host rewrite and auth run before any other response.
MIDDLEWARE = [
    "ws_server.middleware.CanonicalHostMiddleware",
    "ws_server.middleware.ApiKeyAuthMiddleware",
    "ws_server.middleware.HealthCheckAllowHttpMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "ws_server.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

# IMPORTANT:
# - WSGI is kept for admin/compatibility, but production traffic should use ASGI (Daphne/Uvicorn).
WSGI_APPLICATION = "ws_server.wsgi.application"
ASGI_APPLICATION = "ws_server.asgi.application"


# Database (optional; this service can run with sqlite for smoke tests).
#
# IMPORTANT:
# - WebSocket-only services often don't need a database.
# - If you DO set DATABASE_URL, we prefer `dj-database-url` to parse it.
# - To keep boot robust, we fall back to sqlite if DATABASE_URL isn't set.
#
DATABASE_URL = _env("DATABASE_URL", None)
if DATABASE_URL:
    try:
        import dj_database_url  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "DATABASE_URL is set but dj-database-url is not installed. "
            "Either install it (`pip install dj-database-url`) or unset DATABASE_URL."
        ) from exc
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
else:
    DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": str(BASE_DIR / "db.sqlite3")}}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


#
# Channels / Redis configuration
#
# REQUIREMENT: Use RedisChannelLayer (NOT InMemoryChannelLayer)
#
REDIS_URL = _env("REDIS_URL", "redis://127.0.0.1:6379/0")
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            # `hosts` accepts redis:// URLs.
            "hosts": [REDIS_URL],
            # Tunables for production safety.
            "capacity": int(_env("CHANNEL_LAYER_CAPACITY", "1000") or "1000"),
            "expiry": int(_env("CHANNEL_LAYER_EXPIRY", "60") or "60"),
        },
    }
}


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": _env("DJANGO_LOG_LEVEL", "INFO") or "INFO"},
}

# API key auth: if set, all HTTP and WebSocket endpoints (except /health/) require X-API-KEY header.
AUTH_API_KEY = _env("AUTH_API_KEY", "").strip() or None

# LLM service: used by POST /api/thread/connect to trigger LLM WebSocket connection
LLM_SERVICE_URL = _env("LLM_SERVICE_URL", "").rstrip("/")  # e.g. http://llm:8000
# Optional: Bearer token or API key sent to LLM when calling from ws_server (required if LLM/ALB expects Authorization)
LLM_SERVICE_AUTH = _env("LLM_SERVICE_AUTH", "").strip() or None
WS_SERVER_URL = _env("WS_SERVER_URL", "").rstrip("/")  # e.g. ws://localhost:8000
