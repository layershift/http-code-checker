# decorators.py
import os
from django.core.exceptions import PermissionDenied
from apps.infrastructure.models import IPAddress
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

def ip_allow(mode):
    """
    Unified IP allow decorator
    
    Args:
        mode: 'master_only' - only MASTER_IPS from .env
              'all' - MASTER_IPS + all database IPs
    """
    def _method_wrapper(view_method):
        def _arguments_wrapper(request, *args, **kwargs):
            user_ip = retrieve_user_ip(request)
            allowed_ips = []
            source = ""
            
            if mode == 'master_only':
                # Only MASTER_IPS from .env
                master_ips = os.getenv('MASTER_IPS', '')
                if not master_ips:
                    raise PermissionDenied("MASTER_IPS not configured in .env")
                
                allowed_ips = [ip.strip() for ip in master_ips.split(',') if ip.strip()]
                source = "MASTER_IPS"
                
            elif mode == 'all':
                # MASTER_IPS + all database IPs
                master_ips = os.getenv('MASTER_IPS', '')
                if master_ips:
                    master_list = [ip.strip() for ip in master_ips.split(',') if ip.strip()]
                    allowed_ips.extend(master_list)
                
                db_ips = list(IPAddress.objects.all().values_list('ip_address', flat=True))
                allowed_ips.extend(db_ips)
                
                if not allowed_ips:
                    raise PermissionDenied("No IPs found in MASTER_IPS or database")
                
                source = "MASTER_IPS + all database IPs"
                
            else:
                raise PermissionDenied(f"Invalid mode: {mode}. Use 'master_only' or 'all'")
            
            # Check if user IP is allowed
            if not is_ip_allowed(user_ip, allowed_ips):
                raise PermissionDenied(f"Access denied for IP: {user_ip} (not in {source})")
            
            return view_method(request, *args, **kwargs)
        
        return _arguments_wrapper
    
    return _method_wrapper