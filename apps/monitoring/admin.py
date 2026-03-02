from django.contrib import admin
from .models import IPAddress, Site, SiteSnapshot


admin.site.register(IPAddress)
admin.site.register(Site)
admin.site.register(SiteSnapshot)
