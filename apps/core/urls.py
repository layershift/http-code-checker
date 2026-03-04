from django.urls import path
from apps.core import views

urlpatterns = [
    # Dashboard

    path('', views.dashboard, name='dashboard'),

    # Server URLs
    path('servers/', views.ServerListView.as_view(), name='server_list'),
    path('servers/<int:pk>/', views.ServerDetailView.as_view(), name='server_detail'),

    # Site URLs
    path('sites/<int:site_id>/scores/', views.site_score_history, name='site_score_history'),
    path('sites/<int:pk>/', views.SiteDetailView.as_view(), name='site_detail'),
    path('sites/', views.SiteListView.as_view(), name='site_list'),
    
]
