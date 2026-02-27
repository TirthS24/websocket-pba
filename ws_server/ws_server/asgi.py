"""
ASGI config for ws_server project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.0/howto/deployment/asgi/
"""
# Load secrets from AWS Secrets Manager before Django settings are loaded
import ws_server.env_bootstrap  # noqa: F401

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ws_server.settings")

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.core.asgi import get_asgi_application

from ws_server.routing import websocket_urlpatterns
from ws_server.ws_origin import AllowedHostsOrForwardedHostOriginValidator

# Standard Django ASGI application for HTTP.
django_asgi_app = get_asgi_application()

# Channels router for WebSockets.
#
# AllowedHostsOrForwardedHostOriginValidator (when DEBUG is False):
# - Allows when Origin's host is in ALLOWED_HOSTS, or when Origin is missing but
#   Host / X-Forwarded-Host is in ALLOWED_HOSTS (for ALB/proxy where Origin can be dropped).
# - Logs "WebSocket origin denied: ..." when rejecting (origin/host/path, no secrets).
#
# WSS (HTTPS) deployment:
# - Ensure DJANGO_ALLOWED_HOSTS includes your public hostname (e.g. app.example.com).
# - Ensure the reverse proxy forwards Host or X-Forwarded-Host so origin validation can allow.
#
# WHY AuthMiddlewareStack:
# - Provides Django user/session integration if you later rely on cookies/auth.
websocket_app = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
if not settings.DEBUG:
    websocket_app = AllowedHostsOrForwardedHostOriginValidator(websocket_app)

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": websocket_app,
    }
)
