from django.contrib import admin
from .models import IPClass, IPAddress, Site, SiteSnapshot


class IPAddressInline(admin.TabularInline):
    model = IPAddress
    extra = 1  # number of empty rows shown by default
    fields = ("ip_address", "label")
    show_change_link = True


@admin.register(IPClass)
class IPClassAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    inlines = [IPAddressInline]


@admin.register(IPAddress)
class IPAddressAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "ip_class", "created_at")
    list_filter = ("ip_class",)
    search_fields = ("ip_address",)


@admin.register(Site)
class SiteAdmin(admin.ModelAdmin):
    list_display = ("name", "url", "ip_address", "created_at")
    list_filter = ("ip_address",)


@admin.register(SiteSnapshot)
class SiteSnapshotAdmin(admin.ModelAdmin):
    list_display = ("site", "http_status_code", "taken_at")
    list_filter = ("site", "http_status_code")
