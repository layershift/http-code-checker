# monitoring/views.py
from django.shortcuts import render
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Avg, Count, Max, Min, Q
from .models import Site, SiteSnapshot, ScreenshotComparison

@staff_member_required
def comparison_dashboard(request):
    """Dashboard showing all screenshot comparisons"""
    
    # Get recent comparisons (latest 50)
    recent_comparisons = ScreenshotComparison.objects.select_related(
        'site', 'previous_snapshot', 'current_snapshot'
    ).order_by('-created_at')[:50]
    
    # Statistics
    total_comparisons = ScreenshotComparison.objects.count()
    
    # Average SSIM score across all comparisons
    avg_ssim = ScreenshotComparison.objects.aggregate(
        avg=Avg('ssim_score')
    )['avg'] or 0
    
    # Sites with most changes
    sites_by_changes = ScreenshotComparison.objects.values(
        'site__name', 'site__id'
    ).annotate(
        total_comparisons=Count('id'),
        avg_ssim=Avg('ssim_score'),
        avg_change=Avg('percent_difference'),
        max_change=Max('percent_difference'),
        last_comparison=Max('created_at')
    ).order_by('-avg_change')[:10]
    
    # Recent significant changes (more than 5% change)
    significant_changes = ScreenshotComparison.objects.filter(
        percent_difference__gt=5.0
    ).select_related(
        'site', 'previous_snapshot', 'current_snapshot'
    ).order_by('-created_at')[:20]
    
    # Stats by site for cards
    site_stats = Site.objects.annotate(
        comparison_count=Count('comparisons'),
        last_checked=Max('snapshots__taken_at'),
        avg_ssim=Avg('comparisons__ssim_score')
    ).order_by('-comparison_count')[:5]
    
    context = {
        'recent_comparisons': recent_comparisons,
        'total_comparisons': total_comparisons,
        'avg_ssim': avg_ssim,
        'sites_by_changes': sites_by_changes,
        'significant_changes': significant_changes,
        'site_stats': site_stats,
    }
    return render(request, 'monitoring/comparison_dashboard.html', context)
