from django.contrib import admin
from .models import Server, Site, SiteSnapshot


class DomainInline(admin.TabularInline):
    model = Site
    extra = 0
    fields = ("name", "server_ip", "resolved_ip", "is_active")
    show_change_link = True


@admin.register(Server)
class ServerAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    inlines = [DomainInline]


@admin.register(Site)
class DomainAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "server",
        "server_ip",
        "resolved_ip",
        "is_active",
        "created_at",
    )
    list_filter = ("server", "is_active")
    search_fields = ("name",)


@admin.register(SiteSnapshot)
class SiteSnapshotAdmin(admin.ModelAdmin):
    list_display = (
        "site",
        "http_status_code",
        "taken_at",
    )
    list_filter = ("site", "http_status_code")
