from django.db import models
from django.core.validators import validate_ipv46_address
from django.db import models
import ipaddress
from django.utils import timezone
import os 
import socket


def screenshot_upload_path(instance, filename):
    """
    Generate upload path: screenshots/site_name/timestamp_filename
    """
    # Sanitize site name for filesystem (replace spaces/special chars)
    site_name = instance.site.name.replace(' ', '_').lower()
    
    # Create timestamp for filename
    timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
    
    # Keep original extension
    ext = filename.split('.')[-1]
    
    # New filename: site_name_timestamp.png
    new_filename = f"{site_name}_{timestamp}.{ext}"
    
    # Full path: screenshots/site_name/timestamp_filename
    return os.path.join('screenshots', site_name, new_filename)


class Server(models.Model):
    name = models.CharField(max_length=255)

    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name
    
class Site(models.Model):
    name = models.CharField(
        max_length=255,
        unique=True,
        help_text="example.com"
    )

    server = models.ForeignKey(
        Server,
        on_delete=models.CASCADE,
        related_name="domains",
        null=True,
        blank=True
    )

    server_ip = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True,
        null=True,
        blank=True
    )

    resolved_ip = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True,
        null=True,
        blank=True
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    
    def resolve_ip(self):
        """
        Resolve the IPv4 address of the site
        """
        try:
            # Get all IP addresses
            ip_list = socket.gethostbyname_ex(self.name)[2]
            
            # Filter for IPv4 addresses (they're already IPv4 from gethostbyname_ex)
            if ip_list:
                # Store the first IPv4 address
                self.resolved_ip = ip_list[0]
                return self.resolved_ip
            else:
                return None
        except socket.gaierror as e:
            print(f"DNS resolution failed for {self.name}: {e}")
            return None
        except Exception as e:
            print(f"Error resolving IP for {self.name}: {e}")
            return None
    
    def __str__(self):
        return self.name

class SiteSnapshot(models.Model):
    """
    Each monitoring run result.
    """

    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="snapshots"
    )

    screenshot = models.ImageField(upload_to=screenshot_upload_path, null=True, blank=True)

    http_status_code = models.PositiveIntegerField(null=True, blank=True)

    content_length = models.PositiveIntegerField(null=True, blank=True)

    ssim_score = models.FloatField(null=True, blank=True)

    taken_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.site.name} - {self.taken_at}"


