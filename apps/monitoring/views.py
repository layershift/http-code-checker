# monitoring/views.py
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Max, Min, Q
from .models import Site, SiteSnapshot, ScreenshotComparison, Server, SiteScore

@staff_member_required
def comparison_dashboard(request):
    """Dashboard showing all screenshot comparisons with server filter"""
    
    # Get filter parameters
    server_id = request.GET.get('server')
    
    # Base queryset for sites
    sites_queryset = Site.objects.all()
    if server_id:
        sites_queryset = sites_queryset.filter(server_id=server_id)
    
    # Get all servers for filter dropdown
    servers = Server.objects.annotate(
        site_count=Count('domains')
    ).filter(site_count__gt=0).order_by('name')
    
    # Get recent comparisons filtered by server
    comparisons_queryset = ScreenshotComparison.objects.select_related(
        'site', 'previous_snapshot', 'current_snapshot'
    )
    
    if server_id:
        comparisons_queryset = comparisons_queryset.filter(site__server_id=server_id)
    
    recent_comparisons = comparisons_queryset.order_by('-created_at')[:50]
    
    # Statistics
    total_comparisons = comparisons_queryset.count()
    
    # Average SSIM score
    avg_ssim = comparisons_queryset.aggregate(
        avg=Avg('ssim_score')
    )['avg'] or 0
    
    # Sites with most changes (filtered by server)
    sites_by_changes = comparisons_queryset.values(
        'site__name', 'site__id', 'site__server__name'
    ).annotate(
        total_comparisons=Count('id'),
        avg_ssim=Avg('ssim_score'),
        avg_change=Avg('percent_difference'),
        max_change=Max('percent_difference'),
        last_comparison=Max('created_at')
    ).order_by('-avg_change')[:10]
    
    # Recent significant changes (filtered by server)
    significant_changes = comparisons_queryset.filter(
        percent_difference__gt=5.0
    ).select_related(
        'site', 'previous_snapshot', 'current_snapshot'
    ).order_by('-created_at')[:20]
    
    # Stats by site for cards (filtered by server)
    site_stats = sites_queryset.annotate(
        comparison_count=Count('comparisons'),
        last_checked=Max('snapshots__taken_at'),
        avg_ssim=Avg('comparisons__ssim_score')
    ).order_by('-comparison_count')[:5]
    
    # Get selected server name for display
    selected_server = None
    if server_id:
        try:
            selected_server = Server.objects.get(id=server_id)
        except Server.DoesNotExist:
            pass
    
    context = {
        'recent_comparisons': recent_comparisons,
        'total_comparisons': total_comparisons,
        'avg_ssim': avg_ssim,
        'sites_by_changes': sites_by_changes,
        'significant_changes': significant_changes,
        'site_stats': site_stats,
        'servers': servers,
        'selected_server': selected_server,
        'server_id': server_id,
    }
    return render(request, 'monitoring/comparison_dashboard.html', context)


