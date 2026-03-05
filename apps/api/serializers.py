from rest_framework import serializers
from apps.monitoring.models import Server

class ServerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Server
        fields = ['id', 'name', 'description', 'ip_address', 'created_at']
        read_only_fields = ['id', 'created_at']