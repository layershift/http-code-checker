from django.db import models
from django.core.validators import validate_ipv46_address
from django.db import models
import ipaddress


class IPClass(models.Model):
    name = models.CharField(max_length=255, unique=True)
    description = models.TextField(blank=True)

    # New fields for automatic generation
    network = models.CharField(
        max_length=50,
        help_text="Example: 192.168.1.0/24",
        null=True,
        blank=True
    )

    auto_generate = models.BooleanField(
        default=False,
        help_text="Automatically generate IP addresses from this network"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class IPAddress(models.Model):
    """
    Individual IP belonging to a class/group.
    """

    ip_class = models.ForeignKey(
        IPClass,
        on_delete=models.CASCADE,
        related_name="ip_addresses",
        null=True,
        blank=True
    )

    ip_address = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True
    )

    label = models.CharField(max_length=255, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("ip_class", "ip_address")

    def __str__(self):
        return f"{self.ip_address} ({self.ip_class.name})"


class Site(models.Model):
    """
    Website being monitored.
    """

    name = models.CharField(max_length=255)
    url = models.URLField()

    ip_address = models.ForeignKey(
        IPAddress,
        on_delete=models.CASCADE,
        related_name="sites"
    )

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
