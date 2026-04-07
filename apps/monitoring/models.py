# monitoring/models.py
from django.db import models
from django.core.validators import validate_ipv46_address
from django.conf import settings
from django.utils import timezone
import os 
import socket
from django.urls import reverse
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.files.storage import default_storage
from django.db.models.signals import pre_delete
from django.dispatch import receiver
import requests
import logging

logger = logging.getLogger(__name__)

# Import storage class for remote upload
if getattr(settings, 'REMOTE_UPLOADER_ENABLED', False):
    try:
        from .storage import RemoteUploaderStorage
        remote_storage = RemoteUploaderStorage()
        print(f"✅ Using remote storage: {remote_storage}")
    except Exception as e:
        print(f"⚠️ Failed to load remote storage: {e}, falling back to default")
        remote_storage = default_storage
else:
    remote_storage = default_storage

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
        validators=[MinValueValidator(1), MaxValueValidator(1440)],
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
            ip_list = socket.gethostbyname_ex(self.name)[2]
            if ip_list:
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
        return reverse('site_detail', args=[str(self.id)])
    
    def __str__(self):
        return self.name


def delete_remote_file(file_id):
    """
    Helper function to delete a file from remote storage
    Endpoint: DELETE /files/{file_id}?force=true
    """
    if not file_id or not getattr(settings, 'REMOTE_UPLOADER_ENABLED', False):
        return True
    
    try:
        uploader_url = settings.REMOTE_UPLOADER_URL
        # Correct endpoint: /files/{file_id}?force=true
        delete_url = f"{uploader_url}/files/{file_id}?force=true"
        
        print(f"🗑️ Deleting remote file: {delete_url}")
        
        response = requests.delete(delete_url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            print(f"✅ Successfully deleted remote file: {file_id} - {data.get('message', '')}")
            return True
        elif response.status_code == 404:
            print(f"⚠️ Remote file not found (already deleted): {file_id}")
            return True
        else:
            print(f"⚠️ Failed to delete remote file {file_id}: HTTP {response.status_code} - {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error deleting remote file {file_id}: {e}")
        return False
    except Exception as e:
        print(f"❌ Error deleting remote file {file_id}: {e}")
        return False


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
        max_length=500,
        storage=remote_storage
    )

    http_status_code = models.PositiveIntegerField(null=True, blank=True)

    content_length = models.PositiveIntegerField(null=True, blank=True)

    ssim_score = models.FloatField(null=True, blank=True)

    taken_at = models.DateTimeField(auto_now_add=True)

    ticket = models.CharField(max_length=320, null=True, blank=True)
        
    is_baseline = models.BooleanField(
        default=False,
        help_text="If True, this is the baseline snapshot for comparisons"
    )

    class Meta:
        ordering = ['-taken_at']
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
        if self.is_baseline:
            SiteSnapshot.objects.filter(site=self.site, is_baseline=True).exclude(pk=self.pk).update(is_baseline=False)
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        """Delete the associated remote screenshot file before deleting the database record."""
        # Store the file_id before deleting the object
        file_id = self.screenshot.name if self.screenshot else None
        
        # Delete the database record
        super().delete(*args, **kwargs)
        
        # Delete the remote file
        if file_id:
            delete_remote_file(file_id)


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
        help_text="Visual heatmap showing changes",
        storage=remote_storage
    )

    diff_image = models.ImageField(
        upload_to='comparisons/diffs/',
        null=True,
        blank=True,
        help_text="Difference image highlighting changes",
        storage=remote_storage
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['previous_snapshot', 'current_snapshot']

    def __str__(self):
        return f"{self.site.name} - {self.previous_snapshot.taken_at} vs {self.current_snapshot.taken_at} (SSIM: {self.ssim_score:.3f})"

    def delete(self, *args, **kwargs):
        """Delete associated remote files (heatmap and diff) before deleting the database record."""
        # Store file IDs before deletion
        heatmap_id = self.heatmap.name if self.heatmap else None
        diff_id = self.diff_image.name if self.diff_image else None
        
        # Delete the database record
        super().delete(*args, **kwargs)
        
        # Delete remote files
        if heatmap_id:
            delete_remote_file(heatmap_id)
        
        if diff_id:
            delete_remote_file(diff_id)


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
    
    performance_score = models.FloatField(null=True, blank=True)
    seo_score = models.FloatField(null=True, blank=True)
    security_score = models.FloatField(null=True, blank=True)
    availability_score = models.FloatField(null=True, blank=True)
    content_quality_score = models.FloatField(null=True, blank=True)
    
    overall_score = models.FloatField(null=True, blank=True)
    
    page_load_time_ms = models.IntegerField(null=True, blank=True)
    ttfb_ms = models.IntegerField(null=True, blank=True)
    content_size_kb = models.IntegerField(null=True, blank=True)
    has_ssl = models.BooleanField(default=False)
    has_security_headers = models.BooleanField(default=False)
    
    calculated_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-calculated_at']
        indexes = [
            models.Index(fields=['site', '-calculated_at']),
        ]
    
    def __str__(self):
        return f"{self.site.name} - {self.overall_score} - {self.calculated_at}"


# ===== SIGNALS FOR CASCADE DELETES =====
# These signals ensure remote files are deleted even when objects are deleted via cascade

@receiver(pre_delete, sender=SiteSnapshot)
def delete_snapshot_files_signal(sender, instance, **kwargs):
    """
    Delete remote file when a snapshot is deleted (even via cascade)
    This works alongside the delete() method for double coverage
    """
    file_id = instance.screenshot.name if instance.screenshot else None
    if file_id:
        delete_remote_file(file_id)


@receiver(pre_delete, sender=ScreenshotComparison)
def delete_comparison_files_signal(sender, instance, **kwargs):
    """
    Delete remote files when a comparison is deleted (even via cascade)
    This works alongside the delete() method for double coverage
    """
    heatmap_id = instance.heatmap.name if instance.heatmap else None
    diff_id = instance.diff_image.name if instance.diff_image else None
    
    if heatmap_id:
        delete_remote_file(heatmap_id)
    if diff_id:
        delete_remote_file(diff_id)


@receiver(pre_delete, sender=Site)
def delete_site_files_signal(sender, instance, **kwargs):
    """
    Log when a site is deleted.
    Snapshots and comparisons will trigger their own signals via cascade.
    """
    print(f"🗑️ Deleting site: {instance.name} - {instance.snapshots.count()} snapshots, {instance.comparisons.count()} comparisons")
    # No need to manually delete files - the pre_delete signals for snapshots/comparisons will handle it


@receiver(pre_delete, sender=Server)
def delete_server_files_signal(sender, instance, **kwargs):
    """
    Log when a server is deleted.
    Cascade will trigger site deletion, which triggers snapshot/comparison deletion.
    """
    sites_count = instance.domains.count()
    print(f"🗑️ Deleting server: {instance.name} with {sites_count} sites")
    # No need to manually delete files - cascade will trigger site deletion, which triggers snapshot/comparison deletion

# monitoring/models.py - Add this new model

class ZulipMessage(models.Model):
    """
    Tracks Zulip messages sent for monitoring reports
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Processing'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('partial', 'Partial Success'),
    ]
    
    # Message identification
    message_id = models.CharField(
        max_length=255,
        unique=True,
        help_text="Zulip message ID or custom tracking ID"
    )
    
    # Related objects
    server = models.ForeignKey(
        Server,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="zulip_messages"
    )
    
    site = models.ForeignKey(
        Site,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="zulip_messages"
    )
    
    # Message content
    title = models.CharField(max_length=500, blank=True)
    body = models.TextField(blank=True)
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    
    # Evaluation results
    total_sites = models.IntegerField(default=0)
    successful_sites = models.IntegerField(default=0)
    failed_sites = models.IntegerField(default=0)
    warning_sites = models.IntegerField(default=0)
    
    # Detailed results (JSON)
    results_summary = models.JSONField(default=dict, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    
    # Metadata
    ticket_id = models.CharField(max_length=255, null=True, blank=True)
    source = models.CharField(max_length=100, default='api')  # api, cron, manual
    
    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['message_id']),
            models.Index(fields=['server', '-created_at']),
            models.Index(fields=['site', '-created_at']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        return f"{self.message_id} - {self.status} - {self.created_at}"
    
    def get_duration(self):
        """Get processing duration in seconds"""
        if self.processed_at and self.created_at:
            return (self.processed_at - self.created_at).total_seconds()
        return None