from django.urls import path
from . import views
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView


urlpatterns = [
    # path('schema/', SpectacularAPIView.as_view(), name='schema'),
    # path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    # path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # path('test/', views.test_view, name='test'),
    path("servers/", views.handle_servers, name="servers"),
    path("sites/", views.handle_sites, name="sites"),
    path('snapshots/<str:site_name>/', views.list_snapshots, name='api_list_snapshots'),
    path('snapshots/<int:snapshot_id>/status/', views.get_snapshot_status, name='api_snapshot_status'),
    path('snapshots/<int:snapshot_id>/set-baseline/', views.set_snapshot_baseline, name='set_snapshot_baseline'),
    path('snapshots/', views.trigger_snapshot, name='api_trigger_snapshot'),
    path('snapshots/<int:snapshot_id>/set-baseline/', views.set_snapshot_baseline, name='set_snapshot_baseline'),
    path('dispatch_comparison/', views.dispatch_comparison, name='api_dispatch_comparison'),
    path('bash/<str:script>', views.serve_bash_script, name='bash_script'),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    
    path('sites/<str:site_name>/delete/', views.delete_site_by_name, name='delete_site_by_name'),
    path('servers/<str:server_name>/delete/', views.delete_server_by_name, name='delete_server_by_name'),
    path('servers/check-server-baseline/', views.check_server_baseline_health, name='check_server_baseline_health'),
    path('v1/snapshots/<int:snapshot_id>/delete/', views.delete_snapshot_by_id, name='delete_snapshot_by_id'),

    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    path('monitoring/status/', views.get_monitoring_status, name='get_monitoring_status'),
   
    
]
