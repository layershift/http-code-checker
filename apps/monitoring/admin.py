# monitoring/admin.py - Updated with debugging
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.contrib import messages
import threading
from .models import Site, Server, SiteSnapshot
from .utils import capture_screenshot_for_snapshot

class SiteSnapshotInline(admin.TabularInline):
    model = SiteSnapshot
    extra = 1  # Change to 1 to show an empty form
    readonly_fields = ['screenshot_preview', 'taken_at', 'http_status_code', 'content_length']
    fields = ['screenshot_preview', 'taken_at', 'http_status_code', 'content_length', 'ssim_score']

    def screenshot_preview(self, obj):
        if obj and obj.screenshot:
            return format_html(
                '<img src="{}" width="100" style="border-radius: 4px;" />',
                obj.screenshot.url
            )
        return "No screenshot"
    screenshot_preview.short_description = "Preview"

@admin.register(SiteSnapshot)
class SiteSnapshotAdmin(admin.ModelAdmin):
    list_display = ['site', 'taken_at', 'http_status_code', 'content_length', 'has_screenshot']
    list_filter = ['site', 'http_status_code', 'taken_at']
    readonly_fields = ['taken_at', 'screenshot_preview']
    fields = ['site', 'http_status_code', 'content_length', 'ssim_score', 'screenshot', 'taken_at', 'screenshot_preview']
    
    # Make screenshot not required in the form
    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['screenshot'].required = False
        return form

    def has_screenshot(self, obj):
        return bool(obj.screenshot)
    has_screenshot.boolean = True
    has_screenshot.short_description = "Screenshot"

    def screenshot_preview(self, obj):
        if obj and obj.screenshot:
            return format_html(
                '<img src="{}" width="300" style="border-radius: 4px;" />',
                obj.screenshot.url
            )
        return "No screenshot uploaded yet"
    screenshot_preview.short_description = "Preview"

    actions = ['capture_screenshots', 'recapture_failed_screenshots']

    def capture_screenshots(self, request, queryset):
        count = 0
        for snapshot in queryset:
            if not snapshot.screenshot:
                thread = threading.Thread(
                    target=capture_screenshot_for_snapshot,
                    args=(snapshot.id,)
                )
                thread.daemon = True
                thread.start()
                count += 1
                print(f"Started screenshot capture for snapshot {snapshot.id}")  # Debug

        if count:
            self.message_user(
                request,
                f"Started screenshot capture for {count} snapshot(s) in background."
            )
        else:
            self.message_user(
                request,
                "Selected snapshots already have screenshots.",
                level='WARNING'
            )
    capture_screenshots.short_description = "Capture screenshots for selected snapshots"

    def recapture_failed_screenshots(self, request, queryset):
        count = 0
        for snapshot in queryset.filter(http_status_code__gte=400):
            thread = threading.Thread(
                target=capture_screenshot_for_snapshot,
                args=(snapshot.id,)
            )
            thread.daemon = True
            thread.start()
            count += 1
            print(f"Started recapture for snapshot {snapshot.id}")  # Debug

        if count:
            self.message_user(
                request,
                f"Started recapture for {count} failed snapshot(s) in background."
            )
        else:
            self.message_user(
                request,
                "No failed snapshots selected.",
                level='WARNING'
            )
    recapture_failed_screenshots.short_description = "Recapture screenshots for failed snapshots"

    def save_model(self, request, obj, form, change):
        """
        Override save_model to capture screenshot for new snapshots
        """
        print(f"save_model called - change: {change}, pk: {obj.pk}")  # Debug
        
        # Check if this is a new snapshot (no ID yet)
        is_new = not obj.pk
        screenshot_empty = not obj.screenshot
        
        print(f"is_new: {is_new}, screenshot_empty: {screenshot_empty}")  # Debug
        
        # Save the object first
        super().save_model(request, obj, form, change)
        print(f"Object saved with ID: {obj.pk}")  # Debug
        
        # If it's a new snapshot or screenshot is empty, trigger screenshot capture
        if is_new or screenshot_empty:
            print(f"Triggering screenshot capture for snapshot {obj.pk}")  # Debug
            messages.info(request, f"Snapshot created. Capturing screenshot in background...")
            
            # Start background thread to capture screenshot
            thread = threading.Thread(
                target=capture_screenshot_for_snapshot,
                args=(obj.id,)
            )
            thread.daemon = True
            thread.start()

    # Add this to see if the form is being saved
    def response_add(self, request, obj, post_url_continue=None):
        print(f"response_add called for object {obj.pk}")  # Debug
        return super().response_add(request, obj, post_url_continue)

@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'server', 'is_active', 'created_at', 'snapshot_count']
    list_filter = ['server', 'is_active']
    search_fields = ['name']
    inlines = [SiteSnapshotInline]
    readonly_fields = ['resolved_ip', 'snapshot_quick_view']
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'server', 'is_active')
        }),
        ('IP Information', {
            'fields': ('server_ip', 'resolved_ip'),
            'classes': ('collapse',)
        }),
        ('Snapshots', {
            'fields': ('snapshot_quick_view',),
            'classes': ('collapse',)
        }),
    )

    def snapshot_count(self, obj):
        count = obj.snapshots.count()
        url = reverse('admin:monitoring_sitesnapshot_changelist') + f'?site__id={obj.id}'
        return format_html('<a href="{}">{} snapshots</a>', url, count)
    snapshot_count.short_description = "Snapshots"

    def snapshot_quick_view(self, obj):
        snapshots = obj.snapshots.order_by('-taken_at')[:5]
        if not snapshots:
            return "No snapshots yet"

        html = '<div style="display: flex; gap: 10px; flex-wrap: wrap;">'
        for snapshot in snapshots:
            if snapshot.screenshot:
                html += format_html(
                    '<div style="text-align: center;">'
                    '<img src="{}" width="100" style="border-radius: 4px;" />'
                    '<br/><small>{} - {}</small>'
                    '</div>',
                    snapshot.screenshot.url,
                    snapshot.taken_at.strftime('%Y-%m-%d'),
                    snapshot.http_status_code
                )
        html += '</div>'
        return format_html(html)
    snapshot_quick_view.short_description = "Recent Snapshots"

@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at', 'site_count']
    search_fields = ['name']

    def site_count(self, obj):
        return obj.domains.count()
    site_count.short_description = "Sites"
