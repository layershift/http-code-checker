# middleware.py
from django.core.exceptions import PermissionDenied
from django.utils.deprecation import MiddlewareMixin
import os
import ipaddress

def retrieve_user_ip(request):
    """Extract user IP from request headers"""
    user_ip = request.META.get('HTTP_X_FORWARDED_FOR')
    if not user_ip:
        user_ip = request.META.get('HTTP_X_REAL_IP')
    
    if user_ip:
        ip = user_ip.split(',')[-1].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    
    return ip

def is_ip_allowed(ip, allowed_ips):
    """Check if an IP is in the allowed list (supports CIDR notation)"""
    for allowed in allowed_ips:
        try:
            if '/' in allowed:
                network = ipaddress.ip_network(allowed, strict=False)
                if ipaddress.ip_address(ip) in network:
                    return True
            elif ip == allowed:
                return True
        except ValueError:
            if ip == allowed:
                return True
    return False

class AdminIPRestrictionMiddleware(MiddlewareMixin):
    """Restrict admin access to MASTER_IPS only"""
    
    def process_view(self, request, view_func, view_args, view_kwargs):
        # Only apply to admin URLs
        if not request.path.startswith('/admin/'):
            return None
        
        # Get client IP
        user_ip = retrieve_user_ip(request)
        
        # Get allowed IPs from environment
        master_ips = os.getenv('MASTER_IPS', '')
        if not master_ips:
            # If no MASTER_IPS configured, block all access
            raise PermissionDenied("Admin access not configured")
        
        allowed_ips = [ip.strip() for ip in master_ips.split(',') if ip.strip()]
        
        # Check if IP is allowed
        if not is_ip_allowed(user_ip, allowed_ips):
            raise PermissionDenied(f"Admin access denied from IP: {user_ip}")
        
        return None