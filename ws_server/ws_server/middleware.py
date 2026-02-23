"""
Middleware for ws_server.

- CanonicalHostMiddleware: when behind an ALB, health checks and internal
  requests may send Host: <task-ip>:8000. Rewrite to the canonical ALB host
  from ALLOWED_HOSTS so Django accepts the request and the app sees the ALB host.
- HealthCheckAllowHttp: allows ALB health checks over HTTP by preventing
  SECURE_SSL_REDIRECT from redirecting /health/ to HTTPS (avoids 301),
  and exempts /health/ from CORS and CSRF so ALB checks are not blocked.
- ApiKeyAuthMiddleware: requires X-API-KEY header to match AUTH_API_KEY for all
  endpoints except /health/ (when AUTH_API_KEY is set).
"""

from __future__ import annotations

from django.conf import settings
from django.http import JsonResponse


def _is_health_path(request) -> bool:
    path = (request.path or "").rstrip("/") or "/"
    return path == "/health"


def _get_canonical_host() -> str | None:
    """First non-localhost entry in ALLOWED_HOSTS (typically the ALB hostname)."""
    allowed = getattr(settings, "ALLOWED_HOSTS", []) or []
    for h in allowed:
        if h in ("*", "localhost") or h.startswith("127."):
            continue
        return h
    return allowed[0] if allowed else None


class CanonicalHostMiddleware:
    """
    When behind an ALB, requests (e.g. health checks) may have Host set to the
    task private IP (e.g. 10.0.6.44:8000). Rewrite Host to the canonical ALB
    host from ALLOWED_HOSTS so Django's host check passes and the app sees the
    ALB endpoint instead of the instance IP.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.META.get("HTTP_HOST", "")
        host_part = host.split(":")[0].strip() if host else ""
        if host_part.startswith("10."):  # VPC private IP
            canonical = _get_canonical_host()
            if canonical:
                port_suffix = (":" + host.split(":", 1)[1]) if ":" in host else ""
                request.META["HTTP_HOST"] = canonical + port_suffix
        return self.get_response(request)


class ApiKeyAuthMiddleware:
    """
    When AUTH_API_KEY is set, require X-API-KEY header to match for all requests
    except /health/. Returns 401 with JSON body if the key is missing or invalid.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _is_health_path(request):
            return self.get_response(request)
        # Let OPTIONS (CORS preflight) through without API key so browser gets CORS headers.
        if request.method == "OPTIONS":
            return self.get_response(request)

        auth_key = getattr(settings, "AUTH_API_KEY", None)
        if not auth_key:
            return self.get_response(request)

        # Read X-API-KEY: META (HTTP_X_API_KEY) is canonical; headers is fallback (e.g. some ASGI setups)
        provided = (request.META.get("HTTP_X_API_KEY") or "").strip()
        if not provided and hasattr(request, "headers"):
            provided = (request.headers.get("X-Api-Key") or request.headers.get("x-api-key") or "").strip()
        if not provided or provided != auth_key:
            return JsonResponse(
                {"detail": "Missing or invalid API key. Use X-API-KEY header."},
                status=401,
            )
        return self.get_response(request)


class HealthCheckAllowHttpMiddleware:
    """
    Run before SecurityMiddleware. For requests to /health/:
    - Set proxy SSL header so Django does not redirect HTTP -> HTTPS (avoids 301).
    - Set csrf_processing_done so CSRF middleware skips validation.
    - In response, add permissive CORS so ALB is not blocked by CORS.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if _is_health_path(request):
            # Avoid SSL redirect (SecurityMiddleware)
            request.META["HTTP_X_FORWARDED_PROTO"] = "https"
            # Skip CSRF (same effect as @csrf_exempt on the view)
            request.csrf_processing_done = True
        response = self.get_response(request)
        if _is_health_path(request):
            # Allow any origin for health so ALB checks are not blocked
            response["Access-Control-Allow-Origin"] = "*"
        return response
