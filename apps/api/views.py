from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.http import JsonResponse


# monitoring/views.py
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core import serializers
from apps.monitoring.models import Server
import json

@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
def handle_servers(request):
    """
    API endpoint for server management
    GET: List all servers
    POST: Add a new server
    DELETE: Delete a server (expects JSON with server_id)
    """
    
    # GET - List all servers
    if request.method == "GET":
        servers = Server.objects.all().order_by('-created_at')
        data = []
        for server in servers:
            data.append({
                'id': server.id,
                'name': server.name,
                'description': server.description,
                'created_at': server.created_at.isoformat(),
                'sites_count': server.domains.count()  
            })
        return JsonResponse({
            'status': 'success',
            'servers': data
        }, safe=False)
    
    elif request.method == "POST":
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST.dict()
            
            if not data.get('name'):
                return JsonResponse({
                    'status': 'error',
                    'message': 'Server name is required'
                }, status=400)
            
            server = Server.objects.create(
                name=data['name'],
                description=data.get('description', '')
            )
            
            return JsonResponse({
                'status': 'success',
                'message': f'Server "{server.name}" created successfully',
                'server': {
                    'id': server.id,
                    'name': server.name,
                    'description': server.description,
                    'created_at': server.created_at.isoformat()
                }
            }, status=201)
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
    
    elif request.method == "DELETE":
        try:
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.GET.dict()  
            
            server_id = data.get('server_id') or data.get('id')
            
            if not server_id:
                return JsonResponse({
                    'status': 'error',
                    'message': 'server_id is required'
                }, status=400)
            
            try:
                server = Server.objects.get(id=server_id)
            except Server.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Server with id {server_id} not found'
                }, status=404)
            
            sites_count = server.domains.count()
            if sites_count > 0:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Cannot delete server with {sites_count} site(s). Remove sites first.'
                }, status=400)
            
            server_name = server.name
            server.delete()
            
            return JsonResponse({
                'status': 'success',
                'message': f'Server "{server_name}" deleted successfully'
            })
            
        except json.JSONDecodeError:
            return JsonResponse({
                'status': 'error',
                'message': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'status': 'error',
                'message': str(e)
            }, status=500)
        

@api_view(["GET"])
def handle_sites(request):
    return JsonResponse({
        "status": "ok"
    })  