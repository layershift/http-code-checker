from django.contrib import admin
from .models import IPClass, IPAddress




@admin.register(IPClass)
class IPClassAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")
    search_fields = ("name",)
    class Meta:
        verbose_name = "IP Class"
        verbose_name_plural = "IP Classes"


@admin.register(IPAddress)
class IPAddressAdmin(admin.ModelAdmin):
    list_display = ("ip_address", "ip_class", "created_at")
    list_filter = ("ip_class",)
    search_fields = ("ip_address",)
    class Meta:
        verbose_name = "IP Address"
        verbose_name_plural = "IP Addresses"


