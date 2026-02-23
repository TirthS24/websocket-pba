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
from channels.security.websocket import AllowedHostsOriginValidator
from django.conf import settings
from django.core.asgi import get_asgi_application

from ws_server.routing import websocket_urlpatterns

# Standard Django ASGI application for HTTP.
django_asgi_app = get_asgi_application()

# Channels router for WebSockets.
#
# WHY AllowedHostsOriginValidator:
# - Ensures the websocket Origin/Host is consistent with ALLOWED_HOSTS.
# - Prevents cross-site WebSocket hijacking in common deployments.
#
# WHY AuthMiddlewareStack:
# - Provides Django user/session integration if you later rely on cookies/auth.
# - Safe default even if your protocol is session_id-based.
#
# Local-dev note:
# - Some WS clients (CLI tools, Postman-like clients) do NOT send an Origin header.
# - `AllowedHostsOriginValidator` may reject those with 403.
# - In production, keep strict origin validation.
websocket_app = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
if not settings.DEBUG:
    websocket_app = AllowedHostsOriginValidator(websocket_app)

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": websocket_app,
    }
)
