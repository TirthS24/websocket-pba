"""
CSRF token handler for API endpoints.
Provides endpoint to get CSRF tokens for clients.
"""

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_http_methods


@require_http_methods(["GET"])
def get_csrf_token_view(request):
    """View to get CSRF token for clients.
    
    Clients should call this endpoint first to get a CSRF token,
    then include it in subsequent requests via X-CSRFToken header.
    """
    token = get_token(request)
    return JsonResponse({"csrf_token": token})
