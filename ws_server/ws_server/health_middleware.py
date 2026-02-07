"""
Middleware to allow ALB health check requests from private IP addresses.

This middleware must be placed FIRST in MIDDLEWARE settings to work properly.
It modifies the request before Django's ALLOWED_HOSTS validation runs.
"""
import ipaddress
from django.utils.deprecation import MiddlewareMixin


class HealthCheckAllowedHostsMiddleware(MiddlewareMixin):
    """
    Middleware to bypass ALLOWED_HOSTS check for ALB health checks.
    
    ALB health checks come from private IPs (e.g., 10.0.x.x) which aren't
    in ALLOWED_HOSTS. This middleware detects health check requests from
    private IPs and sets a safe HTTP_HOST value.
    
    CRITICAL: This must be the FIRST middleware in settings.MIDDLEWARE
    """
    
    def process_request(self, request):
        """
        Process request before any other middleware.
        This runs before Django's ALLOWED_HOSTS validation.
        """
        # Get the requested path from raw request
        path = request.path_info if hasattr(request, 'path_info') else request.META.get('PATH_INFO', '')
        
        # Check if this is a health check request
        if path == '/health' or path.startswith('/health'):
            # Get the HTTP_HOST header value
            http_host = request.META.get('HTTP_HOST', '')
            
            # Check if HTTP_HOST is a private IP (ALB health check)
            # Extract just the IP part (remove port if present)
            host_ip = http_host.split(':')[0]
            
            try:
                ip_obj = ipaddress.ip_address(host_ip)
                if ip_obj.is_private:
                    # This is a health check from ALB private IP
                    # Replace HTTP_HOST with 'localhost' which should be in ALLOWED_HOSTS
                    # Or use any host that's in your ALLOWED_HOSTS
                    request.META['HTTP_HOST'] = 'localhost'
            except ValueError:
                # Not an IP address (might be domain name), let it pass through normally
                pass
        
        # Return None to continue processing
        return None