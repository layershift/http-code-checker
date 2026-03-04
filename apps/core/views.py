from django.views.generic import ListView, DetailView
from django.db.models import Count, Q
from apps.monitoring.models import Server, Site, SiteSnapshot, SiteScore
from apps.infrastructure.models import IPAddress, IPClass
from django.shortcuts import render, get_object_or_404 
import json
from django.db.models import Avg


# Dashboard view


def dashboard(request):
    # Get statistics
    total_servers = Server.objects.count()
    total_sites = Site.objects.count()
    active_sites = Site.objects.filter(is_active=True).count()
    inactive_sites = Site.objects.filter(is_active=False).count()

    # Get recent sites - FIXED: removed server_ip and resolved_ip from select_related
    recent_sites = Site.objects.select_related('server').order_by('-created_at')[:10]

    # Get servers with their site counts
    servers = Server.objects.annotate(
        site_count=Count('domains'),
        active_site_count=Count('domains', filter=Q(domains__is_active=True))
    ).order_by('name')

    # Calculate percentages for the chart
    if total_sites > 0:
        active_percentage = (active_sites / total_sites) * 100
    else:
        active_percentage = 0

    # Get recent snapshots
    recent_snapshots = SiteSnapshot.objects.select_related('site').order_by('-taken_at')[:5]

    context = {
        'total_servers': total_servers,
        'total_sites': total_sites,
        'active_sites': active_sites,
        'inactive_sites': inactive_sites,
        'active_percentage': active_percentage,
        'recent_sites': recent_sites,
        'servers': servers,
        'recent_snapshots': recent_snapshots,
    }

    return render(request, 'monitoring/dashboard.html', context)


# Server list view
class ServerListView(ListView):
    model = Server
    template_name = 'monitoring/server_list.html'
    context_object_name = 'servers'
    paginate_by = 20

    def get_queryset(self):
        return Server.objects.annotate(
            site_count=Count('domains'),
            active_site_count=Count('domains', filter=Q(domains__is_active=True))
        ).order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_servers'] = Server.objects.count()
        return context


# Server detail view
class ServerDetailView(DetailView):
    model = Server
    template_name = 'monitoring/server_detail.html'
    context_object_name = 'server'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        server = self.object
        
        # Get all sites on this server
        sites = server.domains.all().order_by('name')
        context['sites'] = sites
        
        # Calculate average scores for each site
        for site in sites:
            site.avg_score = SiteScore.objects.filter(
                site=site
            ).aggregate(Avg('overall_score'))['overall_score__avg']
            
            # Get latest score
            latest_score = SiteScore.objects.filter(
                site=site
            ).order_by('-calculated_at').first()
            site.latest_score_value = latest_score.overall_score if latest_score else None
        
        # Calculate server-wide statistics
        context['total_sites'] = sites.count()
        context['active_sites'] = sites.filter(is_active=True).count()
        context['inactive_sites'] = sites.filter(is_active=False).count()
        
        # Calculate average score across all sites
        all_scores = SiteScore.objects.filter(site__in=sites).aggregate(
            avg_score=Avg('overall_score')
        )['avg_score']
        context['server_avg_score'] = round(all_scores, 1) if all_scores else None
        
        # Get sites with highest/lower scores
        sites_with_scores = []
        for site in sites:
            if site.avg_score:
                sites_with_scores.append({
                    'name': site.name,
                    'avg_score': site.avg_score,
                    'url': site.get_absolute_url()
                })
        
        # Sort for top/bottom performers
        context['top_performers'] = sorted(sites_with_scores, key=lambda x: x['avg_score'], reverse=True)[:5]
        context['bottom_performers'] = sorted(sites_with_scores, key=lambda x: x['avg_score'])[:5]
        
        return context


# Site list view
class SiteListView(ListView):
    model = Site
    template_name = 'monitoring/site_list.html'
    context_object_name = 'sites'
    paginate_by = 20

    def get_queryset(self):
        # FIXED: removed server_ip and resolved_ip from select_related
        queryset = Site.objects.select_related('server').all()

        # Filter by status
        status = self.request.GET.get('status')
        if status == 'active':
            queryset = queryset.filter(is_active=True)
        elif status == 'inactive':
            queryset = queryset.filter(is_active=False)

        # Filter by server
        server_id = self.request.GET.get('server')
        if server_id:
            queryset = queryset.filter(server_id=server_id)

        # Search
        search = self.request.GET.get('search')
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(server__name__icontains=search)
                # Removed IP address searches since they're not foreign keys
            )

        return queryset.order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['total_sites'] = Site.objects.count()
        context['active_sites'] = Site.objects.filter(is_active=True).count()
        context['inactive_sites'] = Site.objects.filter(is_active=False).count()
        context['servers'] = Server.objects.all()
        context['current_status'] = self.request.GET.get('status', '')
        context['current_server'] = self.request.GET.get('server', '')
        context['current_search'] = self.request.GET.get('search', '')
        return context


class SiteDetailView(DetailView):
    model = Site
    template_name = 'monitoring/site_detail.html'
    context_object_name = 'site'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # Get all snapshots for this site
        all_snapshots = self.object.snapshots.all().order_by('-taken_at')
        
        # For the history table - show all
        context['snapshots'] = all_snapshots
        
        # For the gallery - only those with screenshots
        context['snapshots_with_screenshots'] = all_snapshots.exclude(
            screenshot=''
        )
        
        # Latest snapshot
        context['latest_snapshot'] = all_snapshots.first()

        # Same server sites
        context['same_server_sites'] = Site.objects.filter(
            server=self.object.server
        ).exclude(
            id=self.object.id
        )[:5]
        
        # ===== ADD SCORE CONTEXT =====
        # Get latest score for this site
        context['latest_score'] = SiteScore.objects.filter(
            site=self.object
        ).order_by('-calculated_at').first()
        
        # Get score count
        context['score_count'] = SiteScore.objects.filter(
            site=self.object
        ).count()
        
        # Optional: Add average score
        context['avg_score'] = SiteScore.objects.filter(
            site=self.object
        ).aggregate(Avg('overall_score'))['overall_score__avg']
        
        # Debug print (remove in production)
        print(f"Site: {self.object.name}, Scores: {context['score_count']}, Latest: {context['latest_score']}")

        return context

# API-like views for AJAX requests (optional)
def get_server_stats(request, pk):
    """Return server statistics as JSON"""
    from django.http import JsonResponse

    try:
        server = Server.objects.get(pk=pk)
        sites = server.domains.all()

        data = {
            'server_name': server.name,
            'total_sites': sites.count(),
            'active_sites': sites.filter(is_active=True).count(),
            'inactive_sites': sites.filter(is_active=False).count(),
            'created_at': server.created_at.isoformat(),
        }
        return JsonResponse(data)
    except Server.DoesNotExist:
        return JsonResponse({'error': 'Server not found'}, status=404)


def get_site_status_chart(request):
    """Return site status data for charts"""
    from django.http import JsonResponse

    active_count = Site.objects.filter(is_active=True).count()
    inactive_count = Site.objects.filter(is_active=False).count()

    data = {
        'labels': ['Active', 'Inactive'],
        'data': [active_count, inactive_count],
        'colors': ['#10B981', '#EF4444'],
    }
    return JsonResponse(data)


def search_sites(request):
    """Search sites and return JSON results"""
    from django.http import JsonResponse

    query = request.GET.get('q', '')
    if len(query) < 2:
        return JsonResponse({'results': []})

    sites = Site.objects.filter(
        Q(name__icontains=query) |
        Q(server__name__icontains=query)
    ).select_related('server')[:10]

    results = [
        {
            'id': site.id,
            'name': site.name,
            'server': site.server.name,
            'is_active': site.is_active,
            'url': f'/monitoring/sites/{site.id}/'
        }
        for site in sites
    ]

    return JsonResponse({'results': results})

def site_score_history(request, site_id):
    """View showing site score evolution over time"""
    site = get_object_or_404(Site, id=site_id)
    
    # Get all scores for this site
    scores = SiteScore.objects.filter(site=site).order_by('calculated_at')
    
    # Prepare data for charts
    dates = [score.calculated_at.strftime('%Y-%m-%d %H:%M') for score in scores]
    overall = [score.overall_score for score in scores]
    performance = [score.performance_score for score in scores]
    seo = [score.seo_score for score in scores]
    security = [score.security_score for score in scores]
    
    # Calculate trends
    if len(scores) > 1:
        first = scores.first().overall_score
        last = scores.last().overall_score
        trend = last - first
        trend_percentage = (trend / first * 100) if first else 0
    else:
        trend = 0
        trend_percentage = 0
    
    context = {
        'site': site,
        'scores': scores,
        'chart_data': json.dumps({
            'dates': dates,
            'overall': overall,
            'performance': performance,
            'seo': seo,
            'security': security,
        }),
        'avg_score': scores.aggregate(Avg('overall_score'))['overall_score__avg'],
        'latest_score': scores.last(),
        'trend': trend,
        'trend_percentage': trend_percentage,
    }
    
    return render(request, 'monitoring/site_scores.html', context)
