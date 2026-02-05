"""
CSRF token handler for API endpoints.
Provides endpoint to get CSRF tokens for clients.
"""

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import ensure_csrf_cookie
from django.conf import settings


@ensure_csrf_cookie
@require_http_methods(["GET"])
def get_csrf_token_view(request):
    """View to get CSRF token for clients.
    
    Clients should call this endpoint once to get a CSRF token and cookie.
    The token can be reused in subsequent requests via X-CSRFToken header.
    The cookie will be set automatically and reused by the browser.
    """
    token = get_token(request)
    response = JsonResponse({"csrf_token": token})
    
    # Explicitly set the CSRF cookie with settings values
    # Settings are configured to use SameSite=None in DEBUG mode for cross-origin support
    # Note: Some browsers require Secure=True with SameSite=None, but localhost is often exempt
    response.set_cookie(
        settings.CSRF_COOKIE_NAME,
        token,
        max_age=settings.CSRF_COOKIE_AGE if hasattr(settings, 'CSRF_COOKIE_AGE') else 31449600,  # 1 year default
        domain=settings.CSRF_COOKIE_DOMAIN if hasattr(settings, 'CSRF_COOKIE_DOMAIN') else None,
        path=settings.CSRF_COOKIE_PATH if hasattr(settings, 'CSRF_COOKIE_PATH') else '/',
        secure=settings.CSRF_COOKIE_SECURE,
        httponly=settings.CSRF_COOKIE_HTTPONLY,
        samesite=settings.CSRF_COOKIE_SAMESITE,
    )
    
    return response
