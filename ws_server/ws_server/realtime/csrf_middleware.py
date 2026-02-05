"""
Custom CSRF middleware that exempts API endpoints with valid API key authentication.
Since API endpoints are already protected by API key auth, they don't need CSRF protection.
"""

from django.middleware.csrf import CsrfViewMiddleware


def get_auth_api_key():
    """Get AUTH_API_KEY from config, with fallback for initialization."""
    try:
        from ws_server.applib.config import config
        return config.AUTH_API_KEY
    except Exception:
        # Config might not be initialized yet during startup
        import os
        return os.environ.get("AUTH_API_KEY", "")


class ApiCsrfMiddleware(CsrfViewMiddleware):
    """
    Custom CSRF middleware that exempts API endpoints with valid API key authentication.
    Falls back to standard CSRF protection for other endpoints.
    """

    def process_view(self, request, callback, callback_args, callback_kwargs):
        # Check if this is an API endpoint with valid API key authentication
        if self._has_valid_api_key(request):
            # Exempt from CSRF protection - API key auth is sufficient
            return None
        
        # For all other requests, use standard CSRF protection
        return super().process_view(request, callback, callback_args, callback_kwargs)
    
    def _has_valid_api_key(self, request) -> bool:
        """Check if request has valid API key authentication."""
        # Extract Authorization header (case-insensitive)
        auth_header = None
        for key, value in request.headers.items():
            if key.lower() == "authorization":
                auth_header = value
                break
        
        if not auth_header:
            return False
        
        expected_key = get_auth_api_key()
        if not expected_key:
            # If no key is configured, don't exempt (use CSRF)
            return False
        
        return auth_header == expected_key
