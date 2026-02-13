"""
Custom CSRF middleware that disables CSRF verification for all requests.
Protection is via API key (AuthMiddleware) only; no @csrf_exempt decorators used.
"""

from django.middleware.csrf import CsrfViewMiddleware


class ApiCsrfMiddleware(CsrfViewMiddleware):
    """
    Disables CSRF verification for all endpoints. API key auth is the only
    required protection (enforced by AuthMiddleware).
    """

    def process_view(self, request, callback, callback_args, callback_kwargs):
        # Never enforce CSRF; always allow the request to continue to the view
        return None
