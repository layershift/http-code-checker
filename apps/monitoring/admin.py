# monitoring/admin.py - Updated with debugging
from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html
from django.contrib import messages
from django.utils.safestring import mark_safe
from .models import Site, Server, SiteSnapshot, ScreenshotComparison, SiteScore
from django.db.models import Avg, Max, Min

from .utils import capture_screenshot_for_snapshot
from .tasks import capture_screenshot_task, create_comparison_task
from django.contrib import admin
from django.contrib.admin import AdminSite
from django.utils.html import format_html
from django.urls import reverse
from django.db.models import Count, Q
from django.utils import timezone
from .models import ZulipMessage, Server, Site, SiteSnapshot, ScreenshotComparison, SiteScore

from django.db.models import Count, Q

class MonitoringAdminSite(AdminSite):
    site_header = 'Monitoring Administration'
    site_title = 'Monitoring Admin'
    
    def get_app_list(self, request):
        app_list = super().get_app_list(request)
        
        # Add custom statistics to the Zulip Messages section
        for app in app_list:
            if app['app_label'] == 'monitoring':
                for model in app['models']:
                    if model['object_name'] == 'ZulipMessage':
                        # Add statistics
                        model['stats'] = {
                            'total': ZulipMessage.objects.count(),
                            'pending': ZulipMessage.objects.filter(status='pending').count(),
                            'processing': ZulipMessage.objects.filter(status='processing').count(),
                            'completed': ZulipMessage.objects.filter(status='completed').count(),
                            'failed': ZulipMessage.objects.filter(status='failed').count(),
                        }
                        break
                break
        
        return app_list

class SiteSnapshotInline(admin.TabularInline):
    model = SiteSnapshot
    extra = 0
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

# monitoring/admin.py - Updated save_model with comparison
# monitoring/admin.py - Fixed comparison_status method

@admin.register(SiteSnapshot)
class SiteSnapshotAdmin(admin.ModelAdmin):
    list_display = ['site', 'taken_at', 'http_status_code', 'content_length', 'is_baseline', 'has_screenshot', 'comparison_status']
    list_filter = ['site', 'http_status_code', 'taken_at', 'is_baseline']
    readonly_fields = ['taken_at', 'screenshot_preview', 'comparison_info']
    fields = ['site', 'http_status_code', 'content_length', 'ssim_score', 'is_baseline', 'screenshot', 'taken_at', 
              'screenshot_preview', 'comparison_info', 'ticket']
    
    actions = ['enqueue_screenshot_capture', 'enqueue_comparison', 'set_as_baseline']
    
    def set_as_baseline(self, request, queryset):
        """Set selected snapshot as baseline (will unset others)"""
        if queryset.count() > 1:
            self.message_user(request, "Please select only one snapshot to set as baseline", level='ERROR')
            return
        
        snapshot = queryset.first()
        if not snapshot.screenshot:
            self.message_user(request, "Cannot set as baseline: snapshot has no screenshot", level='ERROR')
            return
        
        # This will trigger the save() method which handles the unique constraint
        snapshot.is_baseline = True
        snapshot.save()
        self.message_user(request, f"Snapshot {snapshot.id} set as baseline for {snapshot.site.name}")
    set_as_baseline.short_description = "Set as baseline for this site"

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if 'screenshot' in form.base_fields:
            form.base_fields['screenshot'].required = False
        return form

    def has_screenshot(self, obj):
        return bool(obj.screenshot)
    has_screenshot.boolean = True
    has_screenshot.short_description = "Screenshot"
    
    def comparison_status(self, obj):
        """Check if comparison exists for this snapshot"""
        from .models import ScreenshotComparison
        
        # Check if this snapshot is used in any comparison
        if hasattr(obj, 'previous_comparisons') and obj.previous_comparisons.exists():
            comparison = obj.previous_comparisons.first()
            return format_html(
                '<span class="badge" style="background-color: #28a745; color: white; padding: 3px 7px; border-radius: 10px;">'
                'SSIM: {}</span>',
                f"{comparison.ssim_score:.3f}"  # This is the argument for format_html
            )
        elif hasattr(obj, 'next_comparisons') and obj.next_comparisons.exists():
            comparison = obj.next_comparisons.first()
            return format_html(
                '<span class="badge" style="background-color: #28a745; color: white; padding: 3px 7px; border-radius: 10px;">'
                'SSIM: {}</span>',
                f"{comparison.ssim_score:.3f}"  # This is the argument for format_html
            )
        # When no comparison exists, return simple string (no format_html needed)
        return mark_safe(
            '<span class="badge" style="background-color: #ffc107; color: black; padding: 3px 7px; border-radius: 10px;">'
            'Pending</span>'
        )
    comparison_status.short_description = "Comparison"
    
    def comparison_info(self, obj):
        """Display comparison information"""
        from .models import ScreenshotComparison
        
        # Check if this snapshot is used as current snapshot
        comparisons_as_current = ScreenshotComparison.objects.filter(current_snapshot=obj).select_related('previous_snapshot')
        comparisons_as_previous = ScreenshotComparison.objects.filter(previous_snapshot=obj).select_related('current_snapshot')
        
        html = '<div style="margin-top: 10px;">'
        
        if comparisons_as_current.exists():
            for comp in comparisons_as_current:
                html += format_html(
                    '<div style="border: 1px solid #ddd; padding: 8px; margin-bottom: 5px; border-radius: 4px;">'
                    '<strong>Comparison with previous snapshot:</strong><br>'
                    'Previous: {}<br>'
                    'SSIM: {:.4f}<br>'
                    'Change: {:.2f}%<br>'
                    'Changed pixels: {} / {}<br>'
                    '</div>',
                    comp.previous_snapshot.taken_at,
                    comp.ssim_score,
                    comp.percent_difference,
                    comp.changed_pixels,
                    comp.total_pixels
                )
        
        if comparisons_as_previous.exists():
            for comp in comparisons_as_previous:
                html += format_html(
                    '<div style="border: 1px solid #ddd; padding: 8px; margin-bottom: 5px; border-radius: 4px; background-color: #f8f9fa;">'
                    '<strong>Comparison with next snapshot:</strong><br>'
                    'Next: {}<br>'
                    'SSIM: {:.4f}<br>'
                    'Change: {:.2f}%<br>'
                    'Changed pixels: {} / {}<br>'
                    '</div>',
                    comp.current_snapshot.taken_at,
                    comp.ssim_score,
                    comp.percent_difference,
                    comp.changed_pixels,
                    comp.total_pixels
                )
        
        if not (comparisons_as_current.exists() or comparisons_as_previous.exists()):
            html += '<p class="help">No comparisons yet. Comparison will be created automatically after screenshot capture.</p>'
        
        html += '</div>'
        return mark_safe(html)
    comparison_info.short_description = "Comparison Details"

    def screenshot_preview(self, obj):
        if obj and obj.screenshot:
            return format_html(
                '<img src="{}" width="300" style="border-radius: 4px;" />',
                obj.screenshot.url
            )
        return "No screenshot uploaded yet"
    screenshot_preview.short_description = "Preview"

    # Custom actions for RQ
    actions = ['enqueue_screenshot_capture', 'enqueue_comparison']

    def enqueue_screenshot_capture(self, request, queryset):
        """Enqueue screenshot capture for selected snapshots"""
        count = 0
        for snapshot in queryset:
            if not snapshot.screenshot:
                from .tasks import capture_screenshot_task
                job = capture_screenshot_task.delay(snapshot.id, snapshot.site.name, snapshot.site.id)
                count += 1
                self.message_user(request, f"Enqueued screenshot capture for snapshot {snapshot.id} (Job: {job.id})")
        
        if count:
            self.message_user(request, f"Enqueued {count} screenshot capture job(s)")
        else:
            self.message_user(request, "Selected snapshots already have screenshots", level='WARNING')
    enqueue_screenshot_capture.short_description = "Capture screenshots via RQ"

    def enqueue_comparison(self, request, queryset):
        """Enqueue comparison for selected snapshots"""
        count = 0
        for snapshot in queryset:
            if snapshot.screenshot:
                from .tasks import create_comparison_task
                job = create_comparison_task.delay(snapshot.id, snapshot.site.id)
                count += 1
                self.message_user(request, f"Enqueued comparison for snapshot {snapshot.id} (Job: {job.id})")
            else:
                self.message_user(request, f"Snapshot {snapshot.id} has no screenshot yet", level='WARNING')
        
        if count:
            self.message_user(request, f"Enqueued {count} comparison job(s)")
    enqueue_comparison.short_description = "Create comparisons via RQ"

    def save_model(self, request, obj, form, change):
        """
        Override save_model to enqueue RQ jobs for new snapshots
        """
        is_new = not obj.pk
        
        # Save the object first
        super().save_model(request, obj, form, change)
        
        # If it's a new snapshot and no screenshot provided, enqueue RQ jobs
        if is_new and not obj.screenshot:
            from django_rq import get_queue
            from .tasks import capture_screenshot_task, create_comparison_task
            
            # Get the default queue
            queue = get_queue('default')
            
            # Enqueue screenshot capture task
            screenshot_job = queue.enqueue(
                capture_screenshot_task,
                obj.id,
                obj.site.name,
                obj.site.id
            )
            
            # Enqueue comparison task to run AFTER screenshot job
            comparison_job = queue.enqueue(
                create_comparison_task,
                obj.id,
                obj.site.id,
                depends_on=screenshot_job  # This creates the dependency!
            )
            
            messages.info(
                request, 
                f'✅ Snapshot created.<br>'
                f'📸 Screenshot job: {screenshot_job.id[:8]}...<br>'
                f'🔍 Comparison job: {comparison_job.id[:8]}... (will run after screenshot)'
            )
            
            print(f"🚀 Enqueued screenshot job {screenshot_job.id} for snapshot {obj.id}")
            print(f"🔗 Enqueued comparison job {comparison_job.id} (depends on {screenshot_job.id})")
            
        elif is_new and obj.screenshot:
            # If screenshot was provided manually, still try to create comparison
            from django_rq import get_queue
            from .tasks import create_comparison_task
            
            queue = get_queue('default')
            comparison_job = queue.enqueue(
                create_comparison_task,
                obj.id,
                obj.site.id
            )
            messages.info(request, f'Snapshot created with screenshot. Comparison job enqueued: {comparison_job.id[:8]}...')

@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ['name', 'server', 'resolved_ip', 'is_active', 
                   'continuous_monitoring', 'monitoring_frequency', 'created_at']
    list_filter = ['server', 'is_active', 'continuous_monitoring']
    search_fields = ['name', 'resolved_ip']
    readonly_fields = ['resolved_ip', 'created_at']  # Keep created_at readonly
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'server', 'is_active')
        }),
        ('IP Information', {
            'fields': ('server_ip', 'resolved_ip'),
            'classes': ('collapse',)
        }),
        ('Monitoring Settings', {
            'fields': ('continuous_monitoring', 'monitoring_frequency'),
            'description': 'Configure automatic monitoring for this site'
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    actions = ['enable_continuous_monitoring', 'disable_continuous_monitoring', 
               'set_frequency_5min', 'set_frequency_15min', 'set_frequency_60min']
    
    def enable_continuous_monitoring(self, request, queryset):
        updated = queryset.update(continuous_monitoring=True)
        self.message_user(request, f"Enabled continuous monitoring for {updated} sites")
    enable_continuous_monitoring.short_description = "Enable continuous monitoring"
    
    def disable_continuous_monitoring(self, request, queryset):
        updated = queryset.update(continuous_monitoring=False)
        self.message_user(request, f"Disabled continuous monitoring for {updated} sites")
    disable_continuous_monitoring.short_description = "Disable continuous monitoring"
    
    def set_frequency_5min(self, request, queryset):
        updated = queryset.update(monitoring_frequency=5)
        self.message_user(request, f"Set monitoring frequency to 5 minutes for {updated} sites")
    set_frequency_5min.short_description = "Set frequency to 5 minutes"
    
    def set_frequency_15min(self, request, queryset):
        updated = queryset.update(monitoring_frequency=15)
        self.message_user(request, f"Set monitoring frequency to 15 minutes for {updated} sites")
    set_frequency_15min.short_description = "Set frequency to 15 minutes"
    
    def set_frequency_60min(self, request, queryset):
        updated = queryset.update(monitoring_frequency=60)
        self.message_user(request, f"Set monitoring frequency to 60 minutes for {updated} sites")
    set_frequency_60min.short_description = "Set frequency to 60 minutes"

    def snapshot_count(self, obj):
        count = obj.snapshots.count()
        url = reverse('admin:monitoring_sitesnapshot_changelist') + f'?site__id={obj.id}'
        return format_html('<a href="{}">{} snapshots</a>', url, count)
    snapshot_count.short_description = "Snapshots"

    def snapshot_quick_view(self, obj):
        """Display recent snapshots in admin"""
        snapshots = obj.snapshots.order_by('-taken_at')[:5]
        if not snapshots:
            return "No snapshots yet"

        html = '<div style="display: flex; gap: 10px; flex-wrap: wrap;">'
        for snapshot in snapshots:
            if snapshot.screenshot:
                # FIXED: Build HTML string first, then use mark_safe
                html += f'''
                    <div style="text-align: center;">
                        <img src="{snapshot.screenshot.url}" width="100" style="border-radius: 4px;" />
                        <br/>
                        <small>{snapshot.taken_at.strftime('%Y-%m-%d')} - {snapshot.http_status_code}</small>
                    </div>
                '''
        html += '</div>'
        from django.utils.safestring import mark_safe
        return mark_safe(html)
    snapshot_quick_view.short_description = "Recent Snapshots"

@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ['name', 'ip_address', 'created_at', 'site_count']
    list_filter = ['created_at']
    search_fields = ['name', 'ip_address']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'ip_address')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    readonly_fields = ['created_at']
    
    def site_count(self, obj):
        count = obj.domains.count()
        url = reverse('admin:monitoring_site_changelist') + f'?server__id={obj.id}'
        return format_html('<a href="{}">{} site{}</a>', url, count, 's' if count != 1 else '')
    site_count.short_description = "Sites"


@admin.register(ScreenshotComparison)
class ScreenshotComparisonAdmin(admin.ModelAdmin):
    list_display = ['site', 'created_at', 'ssim_score', 'percent_difference', 'changed_pixels', 'total_pixels']
    list_filter = ['site', 'created_at', 'ssim_score']

    # Fields that will be shown
    fieldsets = (
        ('Comparison Info', {
            'fields': ('site', 'created_at')
        }),
        ('Snapshots', {
            'fields': ('previous_snapshot', 'current_snapshot')
        }),
        ('Metrics', {
            'fields': ('ssim_score', 'percent_difference', 'changed_pixels', 'total_pixels')
        }),
        ('Images', {
            'fields': ('heatmap', 'diff_image', 'heatmap_preview', 'diff_preview'),
            'classes': ('wide',)
        }),
    )

    # Make these fields readonly
    readonly_fields = ['created_at', 'heatmap_preview', 'diff_preview']

    def heatmap_preview(self, obj):
        if obj and obj.heatmap:
            return format_html('<img src="{}" width="300" style="border-radius: 4px;" />', obj.heatmap.url)
        return "No heatmap"
    heatmap_preview.short_description = "Heatmap Preview"

    def diff_preview(self, obj):
        if obj and obj.diff_image:
            return format_html('<img src="{}" width="300" style="border-radius: 4px;" />', obj.diff_image.url)
        return "No diff image"
    diff_preview.short_description = "Difference Preview"


@admin.register(SiteScore)
class SiteScoreAdmin(admin.ModelAdmin):
    list_display = ['site', 'overall_score', 'performance_score', 'seo_score', 
                   'security_score', 'availability_score', 'calculated_at']
    list_filter = ['site', 'calculated_at']
    readonly_fields = ['calculated_at']
    
    def changelist_view(self, request, extra_context=None):
        # Add aggregate stats
        extra_context = extra_context or {}
        extra_context['avg_scores'] = SiteScore.objects.aggregate(
            avg_overall=Avg('overall_score'),
            avg_performance=Avg('performance_score'),
            avg_seo=Avg('seo_score'),
            avg_security=Avg('security_score')
        )
        return super().changelist_view(request, extra_context=extra_context)


# monitoring/admin.py
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from .models import ZulipMessage




@admin.register(ZulipMessage)
class ZulipMessageAdmin(admin.ModelAdmin):
    list_display = [
        'message_id', 'status_badge', 'server_link', 'site_link', 
        'progress_bar', 'ticket_id', 'created_at', 'duration_display'
    ]
    list_filter = ['status', 'source', 'created_at']
    search_fields = ['message_id', 'ticket_id', 'server__name', 'site__name']
    readonly_fields = [
        'message_id', 'created_at', 'updated_at', 'processed_at',
        'status_badge', 'progress_bar', 'server_link', 'site_link',
        'body_preview', 'duration_display', 'results_preview', 'progress_display'
    ]
    
    fieldsets = (
        ('Message Information', {
            'fields': ('message_id', 'ticket_id', 'source', 'status_badge')
        }),
        ('Related Objects', {
            'fields': ('server_link', 'site_link', 'title')
        }),
        ('Message Content', {
            'fields': ('body_preview',),
            'classes': ('wide',)
        }),
        ('Progress Tracking', {
            'fields': ('progress_display', 'total_sites', 'sites_processed', 'sites_pending')
        }),
        ('Results', {
            'fields': ('successful_sites', 'failed_sites', 'warning_sites', 'results_preview')
        }),
        ('Timing', {
            'fields': ('created_at', 'updated_at', 'processed_at', 'duration_display')
        }),
    )
    
    def status_badge(self, obj):
        colors = {
            'pending': '#6c757d',
            'processing': '#007bff',
            'completed': '#28a745',
            'failed': '#dc3545',
            'partial': '#fd7e14',
        }
        color = colors.get(obj.status, '#6c757d')
        # FIXED: Pass the color as a single argument with proper placeholder
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 12px; font-size: 12px; font-weight: bold;">{}</span>',
            color, obj.status.upper()
        )
    status_badge.short_description = 'Status'
    
    def server_link(self, obj):
        if obj.server:
            url = reverse('admin:monitoring_server_change', args=[obj.server.id])
            return format_html('<a href="{}">{}</a>', url, obj.server.name)
        return '-'
    server_link.short_description = 'Server'
    
    def site_link(self, obj):
        if obj.site:
            url = reverse('admin:monitoring_site_change', args=[obj.site.id])
            return format_html('<a href="{}">{}</a>', url, obj.site.name)
        return '-'
    site_link.short_description = 'Site'
    
    def progress_bar(self, obj):
        if obj.total_sites == 0:
            return 'N/A'
        percentage = int((obj.sites_processed / obj.total_sites) * 100)
        
        if obj.status == 'completed':
            bg_color = '#28a745'
        elif obj.status == 'failed':
            bg_color = '#dc3545'
        elif obj.status == 'partial':
            bg_color = '#fd7e14'
        elif obj.status == 'processing':
            bg_color = '#007bff'
        else:
            bg_color = '#6c757d'
        
        # FIXED: Proper format_html with arguments
        return format_html(
            '<div style="width: 100%; background-color: #e9ecef; border-radius: 10px; overflow: hidden;">'
            '<div style="width: {}%; background-color: {}; color: white; text-align: center; font-size: 11px; padding: 2px;">{}%</div>'
            '</div>',
            percentage, bg_color, percentage
        )
    progress_bar.short_description = 'Progress'
    
    def progress_display(self, obj):
        if obj.total_sites == 0:
            return "No sites to monitor"
        percentage = int((obj.sites_processed / obj.total_sites) * 100)
        return format_html(
            '<div style="margin-bottom: 10px;">'
            '<strong>{}/{} sites processed ({}%)</strong><br>'
            '<progress value="{}" max="100" style="width: 100%; height: 20px;"></progress>'
            '</div>',
            obj.sites_processed, obj.total_sites, percentage, percentage
        )
    progress_display.short_description = 'Progress Details'
    
    def body_preview(self, obj):
        if not obj.body:
            return "-"
        if len(obj.body) > 500:
            return format_html(
                '<div style="max-height: 150px; overflow-y: auto; background-color: #f8f9fa; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 12px;">'
                '<strong>Preview:</strong><br>{}{}</div>',
                obj.body[:500], '...'
            )
        return format_html(
            '<div style="max-height: 150px; overflow-y: auto; background-color: #f8f9fa; padding: 10px; border-radius: 4px; font-family: monospace; font-size: 12px;">{}</div>',
            obj.body
        )
    body_preview.short_description = 'Message Body'
    
    def results_preview(self, obj):
        html = '<div style="background-color: #f8f9fa; padding: 10px; border-radius: 4px;">'
        
        if obj.successful_sites > 0:
            html += f'<div style="color: #28a745;">✅ Successful: {obj.successful_sites}</div>'
        if obj.failed_sites > 0:
            html += f'<div style="color: #dc3545;">❌ Failed: {obj.failed_sites}</div>'
        if obj.warning_sites > 0:
            html += f'<div style="color: #fd7e14;">⚠️ Warnings: {obj.warning_sites}</div>'
        
        if obj.results_summary:
            if obj.results_summary.get('failed_sites'):
                html += '<div style="margin-top: 8px;"><strong>Failed sites:</strong><br>'
                for site in obj.results_summary['failed_sites'][:5]:
                    html += f'<span style="background-color: #dc3545; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin: 2px; display: inline-block;">{site}</span>'
                if len(obj.results_summary.get('failed_sites', [])) > 5:
                    html += f'<span>... and {len(obj.results_summary["failed_sites"]) - 5} more</span>'
                html += '</div>'
            
            if obj.results_summary.get('warning_sites'):
                html += '<div style="margin-top: 8px;"><strong>Warning sites:</strong><br>'
                for site in obj.results_summary['warning_sites'][:5]:
                    html += f'<span style="background-color: #fd7e14; color: white; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin: 2px; display: inline-block;">{site}</span>'
                if len(obj.results_summary.get('warning_sites', [])) > 5:
                    html += f'<span>... and {len(obj.results_summary["warning_sites"]) - 5} more</span>'
                html += '</div>'
        
        html += '</div>'
        # FIXED: Use mark_safe for HTML content without placeholders
        from django.utils.safestring import mark_safe
        return mark_safe(html)
    results_preview.short_description = 'Results Summary'
    
    def duration_display(self, obj):
        if obj.processed_at:
            duration = (obj.processed_at - obj.created_at).total_seconds()
            if duration < 60:
                return f"{duration:.1f} seconds"
            elif duration < 3600:
                return f"{duration / 60:.1f} minutes"
            else:
                return f"{duration / 3600:.1f} hours"
        return "Still processing"
    duration_display.short_description = 'Duration'
    
    actions = ['mark_as_completed', 'mark_as_failed', 'retry_failed']
    
    def mark_as_completed(self, request, queryset):
        updated = queryset.update(status='completed', processed_at=timezone.now())
        self.message_user(request, f"Marked {updated} message(s) as completed")
    mark_as_completed.short_description = "Mark as completed"
    
    def mark_as_failed(self, request, queryset):
        updated = queryset.update(status='failed', processed_at=timezone.now())
        self.message_user(request, f"Marked {updated} message(s) as failed")
    mark_as_failed.short_description = "Mark as failed"
    
    def retry_failed(self, request, queryset):
        updated = queryset.filter(status='failed').update(status='pending', processed_at=None)
        self.message_user(request, f"Reset {updated} message(s) to pending")
    retry_failed.short_description = "Retry failed messages"
    
    def has_add_permission(self, request):
        return False