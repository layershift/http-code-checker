# urls.py
from django.urls import path
from . import views

urlpatterns = [
    # ... your existing urls ...
    path('comparison/', views.comparison_dashboard, name='comparison_dashboard'),
]
