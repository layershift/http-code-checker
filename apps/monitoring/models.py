from django.db import models
from django.core.validators import validate_ipv46_address
from django.db import models
import ipaddress
from django.utils import timezone
import os 
import socket
from django.urls import reverse
from django.core.validators import MinValueValidator, MaxValueValidator

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

    ip_address = models.GenericIPAddressField(
        protocol='IPv4',
        null=True,
        blank=True,
        help_text="Server IPv4 address"
    )

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

    continuous_monitoring = models.BooleanField(
        default=False,
        help_text="Enable automatic periodic monitoring"
    )

    monitoring_frequency = models.PositiveIntegerField(
        default=3,
        validators=[MinValueValidator(1), MaxValueValidator(1440)],  # Min 1 minute, Max 24 hours
        help_text="Monitoring frequency in minutes (1-1440)"
    )

    is_active = models.BooleanField(default=True)

    last_monitored = models.DateTimeField(
            null=True, 
            blank=True,
            help_text="Last time this site was automatically monitored"
        )

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
    
    
        
    def get_absolute_url(self):
        """Return the URL to access this specific site"""
        return reverse('site_detail', args=[str(self.id)])
    
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

    screenshot = models.ImageField(
        upload_to=screenshot_upload_path, 
        null=True, 
        blank=True,
        max_length=500
    )

    http_status_code = models.PositiveIntegerField(null=True, blank=True)

    content_length = models.PositiveIntegerField(null=True, blank=True)

    ssim_score = models.FloatField(null=True, blank=True)

    taken_at = models.DateTimeField(auto_now_add=True)
    
    # NEW: Baseline field
    is_baseline = models.BooleanField(
        default=False,
        help_text="If True, this is the baseline snapshot for comparisons"
    )

    class Meta:
        ordering = ['-taken_at']
        # Ensure only one baseline per site
        constraints = [
            models.UniqueConstraint(
                fields=['site', 'is_baseline'],
                condition=models.Q(is_baseline=True),
                name='unique_baseline_per_site'
            )
        ]

    def __str__(self):
        baseline = " [BASELINE]" if self.is_baseline else ""
        return f"{self.site.name} - {self.taken_at}{baseline}"

    def save(self, *args, **kwargs):
        """Override save to handle baseline logic"""
        if self.is_baseline:
            # If this is being set as baseline, remove baseline from all other snapshots of this site
            SiteSnapshot.objects.filter(site=self.site, is_baseline=True).exclude(pk=self.pk).update(is_baseline=False)
        super().save(*args, **kwargs)



# models.py - Add this new model
class ScreenshotComparison(models.Model):
    """
    Links two consecutive screenshots to track changes between monitoring runs
    """
    site = models.ForeignKey(
        Site,
        on_delete=models.CASCADE,
        related_name="comparisons"
    )

    previous_snapshot = models.ForeignKey(
        SiteSnapshot,
        on_delete=models.CASCADE,
        related_name="next_comparisons"
    )

    current_snapshot = models.ForeignKey(
        SiteSnapshot,
        on_delete=models.CASCADE,
        related_name="previous_comparisons"
    )

    # Comparison metrics
    ssim_score = models.FloatField(
        null=True,
        blank=True,
        help_text="Structural Similarity Index (1 = identical, 0 = completely different)"
    )

    percent_difference = models.FloatField(
        null=True,
        blank=True,
        help_text="Percentage of pixels that changed"
    )

    changed_pixels = models.IntegerField(
        null=True,
        blank=True,
        help_text="Number of pixels that changed"
    )

    total_pixels = models.IntegerField(
        null=True,
        blank=True,
        help_text="Total number of pixels in the image"
    )

    heatmap = models.ImageField(
        upload_to='comparisons/heatmaps/',
        null=True,
        blank=True,
        help_text="Visual heatmap showing changes"
    )

    diff_image = models.ImageField(
        upload_to='comparisons/diffs/',
        null=True,
        blank=True,
        help_text="Difference image highlighting changes"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['previous_snapshot', 'current_snapshot']  # Prevent duplicates

    def __str__(self):
        return f"{self.site.name} - {self.previous_snapshot.taken_at} vs {self.current_snapshot.taken_at} (SSIM: {self.ssim_score:.3f})"

class SiteScore(models.Model):
    """
    Stores various quality scores for a site over time
    """
    site = models.ForeignKey(
        Site, 
        on_delete=models.CASCADE,
        related_name="scores"
    )
    snapshot = models.OneToOneField(
        SiteSnapshot,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="score"
    )
    
    # Score components (normalized 0-100)
    performance_score = models.FloatField(null=True, blank=True)
    seo_score = models.FloatField(null=True, blank=True)
    security_score = models.FloatField(null=True, blank=True)
    availability_score = models.FloatField(null=True, blank=True)
    content_quality_score = models.FloatField(null=True, blank=True)
    
    # Overall composite score (weighted average)
    overall_score = models.FloatField(null=True, blank=True)
    
    # Raw metrics that feed into scores
    page_load_time_ms = models.IntegerField(null=True, blank=True)
    ttfb_ms = models.IntegerField(null=True, blank=True)
    content_size_kb = models.IntegerField(null=True, blank=True)
    has_ssl = models.BooleanField(default=False)
    has_security_headers = models.BooleanField(default=False)
    
    # Metadata
    calculated_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-calculated_at']
        indexes = [
            models.Index(fields=['site', '-calculated_at']),
        ]
    
    def __str__(self):
        return f"{self.site.name} - {self.overall_score} - {self.calculated_at}"