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
    path('snapshots/', views.trigger_snapshot, name='api_trigger_snapshot'),
    path('dispatch_comparison/', views.dispatch_comparison, name='api_dispatch_comparison'),
    path('bash/<str:script>', views.serve_bash_script, name='bash_script'),
    path('schema/', SpectacularAPIView.as_view(), name='schema'),
    path('docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    # path('api/v1/comparisons/', views.trigger_comparison, name='api_trigger_comparison'),
    # path('api/v1/comparisons/<str:site_name>/', views.get_comparisons, name='api_get_comparisons'),
   
    
]
