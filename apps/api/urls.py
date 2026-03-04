from django.urls import path
from . import views

urlpatterns = [
    path("servers/", views.handle_servers, name="servers"),
    path("sites/", views.handle_sites, name="sites"),

   
    
]
