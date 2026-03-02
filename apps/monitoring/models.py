from django.db import models
from django.core.validators import validate_ipv46_address


class IPAddress(models.Model):
    name = models.CharField(max_length=255)

    ip_address = models.GenericIPAddressField(
        protocol="both",
        unpack_ipv4=True
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.name} ({self.ip_address})"


class Site(models.Model):
    name = models.CharField(max_length=255)

    ip_address = models.ForeignKey(
        IPAddress,
        on_delete=models.CASCADE,
        related_name="sites"
    )

    url = models.URLField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class SiteSnapshot(models.Model):
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="snapshots"
    )

    screenshot = models.ImageField(
        upload_to="screenshots/"
    )

    http_status_code = models.PositiveIntegerField()

    content_length = models.PositiveIntegerField(null=True, blank=True)

    ssim_score = models.FloatField(null=True, blank=True)

    taken_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.site.name} - {self.taken_at}"
