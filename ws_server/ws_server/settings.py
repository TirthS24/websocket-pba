"""
Production-ready settings for Django + Channels (ASGI).

Key requirements implemented:
- Django + Django Channels (ASGI)
- InMemoryChannelLayer (works with ALB sticky sessions)
- Environment-based configuration
- Works behind AWS ALB with sticky sessions enabled
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv

    env_paths = [
        BASE_DIR.parent.parent / ".env",
        BASE_DIR.parent / ".env",
        BASE_DIR / ".env",
        Path(".env"),
    ]
    
    for env_path in env_paths:
        if env_path.exists():
            load_dotenv(env_path, override=False)
            break
    else:
        # If no .env found, try default location (current directory)
        load_dotenv(override=False)
except Exception:
    pass


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

# CSRF protection
CSRF_SECRET_KEY = _env("CSRF_SECRET_KEY", None)
# CSRF settings
# For cross-origin requests, we need SameSite=None and Secure=True
# In DEBUG mode (localhost), we can use Lax; in production with CORS, use None
CSRF_COOKIE_HTTPONLY = True
CSRF_USE_SESSIONS = False
# Allow CSRF token in header for API endpoints
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"
CSRF_COOKIE_NAME = "csrftoken"
# CSRF cookie settings for cross-origin support
# For cross-origin CORS requests, we need SameSite=None
# Note: Most browsers require Secure=True with SameSite=None, but localhost is often exempt
# In DEBUG mode with CORS enabled, use SameSite=None to allow cross-origin cookie sharing
if DEBUG:
    # In DEBUG mode with CORS, use SameSite=None to allow cross-origin requests
    # Some browsers (Chrome, Firefox) allow SameSite=None with Secure=False for localhost
    CSRF_COOKIE_SAMESITE = "None"
    # Allow environment override, but default to False for HTTP in DEBUG mode
    CSRF_COOKIE_SECURE = _env_bool("DJANGO_CSRF_COOKIE_SECURE", default=False)
else:
    CSRF_COOKIE_SAMESITE = "None"
    # In production, Secure must be True for SameSite=None
    CSRF_COOKIE_SECURE = _env_bool("DJANGO_CSRF_COOKIE_SECURE", default=True)

ALLOWED_HOSTS = _env_csv("DJANGO_ALLOWED_HOSTS", default=["localhost","127.0.0.1"])
ALLOWED_HOSTS = [host.strip() for host in ALLOWED_HOSTS if host.strip()]

# When serving behind an ALB, Django must respect X-Forwarded-* headers.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Optional hardening toggles (recommended in production).
SESSION_COOKIE_SECURE = _env_bool("DJANGO_SESSION_COOKIE_SECURE", default=not DEBUG)
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", default=False)
SECURE_SSL_REDIRECT_EXEMPT = ["/health/"]

# If you terminate TLS at the ALB, set HSTS at Django *only* if all traffic is HTTPS.
SECURE_HSTS_SECONDS = int(_env("DJANGO_SECURE_HSTS_SECONDS", "0" if DEBUG else "0") or "0")
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)
SECURE_HSTS_PRELOAD = _env_bool("DJANGO_SECURE_HSTS_PRELOAD", default=False)


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # CORS headers must be before other apps
    "corsheaders",
    # Channels must be installed to enable ASGI + websocket routing.
    "channels",
    # Our websocket logic.
    "ws_server.realtime.apps.RealtimeConfig",
    # Main project app (for graph initialization)
    "ws_server.apps.WsServerConfig",
]

MIDDLEWARE = [
    "ws_server.health_middleware.HealthCheckAllowedHostsMiddleware",  # Allow health checks from ALB private IPs
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",  # CORS middleware should be as high as possible
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "ws_server.realtime.csrf_middleware.ApiCsrfMiddleware",  # Custom CSRF middleware that exempts API endpoints with API key auth
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Authorization middleware for API key validation
    "ws_server.realtime.middleware.AuthMiddleware",
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
# Channels configuration
#
# Using InMemoryChannelLayer since ALB sticky sessions ensure all connections
# for a session go to the same instance, eliminating the need for cross-instance
# communication via Redis.
#
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": _env("DJANGO_LOG_LEVEL", "INFO") or "INFO"},
}

# AppData folder path for LangGraph prompts and templates
# Defaults to ws_server/appdata relative to BASE_DIR
APPDATA_FOLDER_PATH = _env("APPDATA_FOLDER_PATH", None)
if not APPDATA_FOLDER_PATH:
    # BASE_DIR is ws_server/ws_server, so appdata is at ws_server/ws_server/appdata
    APPDATA_FOLDER_PATH = str(BASE_DIR / "appdata")

#
# CORS configuration — allow all origins for now (FE client + AUTH/CSRF in place).
# Restrict CORS_ALLOWED_ORIGINS in production when ready.
#
CORS_ALLOW_ALL_ORIGINS = True

# CSRF trusted origins (required for cross-origin CSRF validation).
# Allow any origin scheme+host for now; tighten in production if needed.
CSRF_TRUSTED_ORIGINS = ["http://localhost", "https://localhost", "http://192.168.2.167", "http://127.0.0.1"]
# Optional: extend via env, e.g. CORS_ALLOWED_ORIGINS or CSRF_TRUSTED_ORIGINS
_extra_origins = _env_csv("CORS_ALLOWED_ORIGINS", default="")
if _extra_origins:
    CSRF_TRUSTED_ORIGINS = list(set(CSRF_TRUSTED_ORIGINS + _extra_origins))

# Allow credentials (cookies, authorization headers) to be sent
CORS_ALLOW_CREDENTIALS = True
# Allow common headers
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "dnt",
    "origin",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]
# Allow all methods for CORS
CORS_ALLOW_METHODS = [
    "DELETE",
    "GET",
    "OPTIONS",
    "PATCH",
    "POST",
    "PUT",
]