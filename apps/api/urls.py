from django.urls import path
from . import views

urlpatterns = [
    path("servers/", views.handle_servers, name="servers"),
    path("sites/", views.handle_sites, name="sites"),
    path('snapshots/<str:site_name>/', views.list_snapshots, name='api_list_snapshots'),
    path('snapshots/<int:snapshot_id>/status/', views.get_snapshot_status, name='api_snapshot_status'),
    path('snapshots/', views.trigger_snapshot, name='api_trigger_snapshot'),
   
    # path('api/v1/comparisons/', views.trigger_comparison, name='api_trigger_comparison'),
    # path('api/v1/comparisons/<str:site_name>/', views.get_comparisons, name='api_get_comparisons'),
   
    
]
