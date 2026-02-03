"""
Authorization middleware for Django HTTP and WebSocket requests.
Validates AUTH_API_KEY for all protected endpoints.
"""

from django.http import JsonResponse
from channels.middleware import BaseMiddleware
from channels.db import database_sync_to_async

# Public paths excluded from authorization
PUBLIC_PATHS = {"/", "/health", "/admin/"}


def get_auth_api_key():
    """Get AUTH_API_KEY from config, with fallback for initialization."""
    try:
        from ws_server.applib.config import config
        return config.AUTH_API_KEY
    except Exception:
        # Config might not be initialized yet during startup
        import os
        return os.environ.get("AUTH_API_KEY", "")


class AuthMiddleware:
    """
    Django middleware for HTTP request authorization.
    Validates AUTH_API_KEY in Authorization header.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Allow OPTIONS requests (CORS preflight) and public paths
        if request.method == "OPTIONS" or request.path in PUBLIC_PATHS:
            return self.get_response(request)

        # Extract Authorization header (case-insensitive)
        auth_header = None
        for key, value in request.headers.items():
            if key.lower() == "authorization":
                auth_header = value
                break

        # Validate authorization
        error_response = self._validate_authorization(auth_header)
        if error_response:
            return error_response

        return self.get_response(request)

    def _validate_authorization(self, auth_header: str | None) -> JsonResponse | None:
        """Validate authorization header against API key. Returns error response or None if valid."""
        if not auth_header:
            return JsonResponse({"detail": "Authorization header missing"}, status=401)
        expected_key = get_auth_api_key()
        if not expected_key:
            # If no key is configured, allow request (for development)
            return None
        if auth_header != expected_key:
            return JsonResponse({"detail": "Invalid authorization key"}, status=401)
        return None


class WebSocketAuthMiddleware(BaseMiddleware):
    """
    Channels middleware for WebSocket connection authorization.
    Validates AUTH_API_KEY in subprotocol or query parameters.
    """

    async def __call__(self, scope, receive, send):
        # Allow public WebSocket paths (if any)
        path = scope.get("path", "")
        if path in PUBLIC_PATHS:
            return await super().__call__(scope, receive, send)

        # Extract Authorization from headers or query string
        auth_header = None
        
        # Check headers (case-insensitive)
        headers = dict(scope.get("headers", []))
        for key, value in headers.items():
            if key.lower() == b"authorization":
                auth_header = value.decode("utf-8")
                break

        # If not in headers, check query string
        if not auth_header:
            query_string = scope.get("query_string", b"").decode("utf-8")
            if query_string:
                params = dict(param.split("=") for param in query_string.split("&") if "=" in param)
                auth_header = params.get("authorization") or params.get("auth")

        # Validate authorization
        expected_key = get_auth_api_key()
        if not expected_key:
            # If no key is configured, allow connection (for development)
            return await super().__call__(scope, receive, send)

        if not auth_header:
            await send({
                "type": "websocket.close",
                "code": 4401,
                "reason": "Authorization header missing",
            })
            return

        if auth_header != expected_key:
            await send({
                "type": "websocket.close",
                "code": 4401,
                "reason": "Invalid authorization key",
            })
            return

        return await super().__call__(scope, receive, send)
