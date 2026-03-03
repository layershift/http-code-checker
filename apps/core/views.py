from django.shortcuts import render
from django.views.generic import ListView, DetailView
from django.db.models import Count, Q
from apps.monitoring.models import Server, Site, SiteSnapshot
from apps.infrastructure.models import IPAddress, IPClass

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
        # FIXED: removed server_ip and resolved_ip from select_related
        context['sites'] = self.object.domains.all()
        context['active_sites'] = self.object.domains.filter(is_active=True).count()
        context['inactive_sites'] = self.object.domains.filter(is_active=False).count()
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


# Site detail view
class SiteDetailView(DetailView):
    model = Site
    template_name = 'monitoring/site_detail.html'
    context_object_name = 'site'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Get snapshots for this site
        context['snapshots'] = self.object.snapshots.all().order_by('-taken_at')[:10]
        context['latest_snapshot'] = self.object.snapshots.order_by('-taken_at').first()

        # Get all sites on the same server
        context['same_server_sites'] = Site.objects.filter(
            server=self.object.server
        ).exclude(
            id=self.object.id
        )[:5]

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
