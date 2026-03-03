from django.db import models
from django.core.validators import validate_ipv46_address
from django.db import models
import ipaddress



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

    screenshot = models.ImageField(upload_to="screenshots/")

    http_status_code = models.PositiveIntegerField()

    content_length = models.PositiveIntegerField(null=True, blank=True)

    ssim_score = models.FloatField(null=True, blank=True)

    taken_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.site.name} - {self.taken_at}"


