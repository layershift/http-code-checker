from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.http import JsonResponse

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.db.models import Count
from apps.monitoring.models import Server, Site, SiteSnapshot, ScreenshotComparison
import json
import ipaddress

def get_client_ip(request):
    """Extract client IP from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE"])
def handle_servers(request):
    """
    API endpoint for server management
    GET: List all servers with stats
    POST: Add a new server (ip_address defaults to requester's IP if not provided)
    DELETE: Delete a server with optional cascade
    """
    
    # ========== GET - List all servers ==========
    if request.method == "GET":
        servers = Server.objects.annotate(
            sites_count=Count('domains'),
            snapshots_count=Count('domains__snapshots'),
            comparisons_count=Count('domains__comparisons')
        ).order_by('-created_at')
        
        data = []
        for server in servers:
            # Detect IP version if IP exists
            ip_version = None
            if server.ip_address:
                try:
                    ip = ipaddress.ip_address(server.ip_address)
                    ip_version = ip.version  # 4 or 6
                except:
                    ip_version = 'unknown'
            
            data.append({
                'id': server.id,
                'name': server.name,
                'description': server.description,
                'ip_address': server.ip_address,
                'ip_version': ip_version,
                'created_at': server.created_at.isoformat(),
                'stats': {
                    'sites': server.sites_count,
                    'snapshots': server.snapshots_count,
                    'comparisons': server.comparisons_count
                }
            })
        
        return JsonResponse({
            'status': 'success',
            'servers': data
        })
    
    # ========== POST - Add a new server ==========
    elif request.method == "POST":
        try:
            # Parse JSON data
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST.dict()
            
            # Validate required fields
            if not data.get('name'):
                return JsonResponse({
                    'status': 'error',
                    'message': 'Server name is required'
                }, status=400)
            
            # Get IP address - either from payload or from requester
            ip_address = data.get('ip_address')
            
            if not ip_address:
                # No IP provided, use requester's IP
                ip_address = get_client_ip(request)
                print(f"📡 No IP provided, using requester's IP: {ip_address}")
                
                # Handle localhost/IPv6 cases
                if ip_address == '::1':
                    ip_address = '127.0.0.1'
                elif ip_address == '::ffff:127.0.0.1':
                    ip_address = '127.0.0.1'
            
            # Validate IP address
            try:
                ip = ipaddress.ip_address(ip_address)
                print(f"✅ Valid IP address: {ip} (IPv{ip.version})")
            except ValueError as e:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Invalid IP address: {ip_address}'
                }, status=400)
            
            # Create server
            server = Server.objects.create(
                name=data['name'],
                description=data.get('description', ''),
                ip_address=ip_address
            )
            
            # Determine IP version for response
            ip_version = ip.version
            
            return JsonResponse({
                'status': 'success',
                'message': f'Server "{server.name}" created successfully',
                'server': {
                    'id': server.id,
                    'name': server.name,
                    'description': server.description,
                    'ip_address': server.ip_address,
                    'ip_version': ip_version,
                    'ip_source': 'payload' if data.get('ip_address') else 'requester',
                    'created_at': server.created_at.isoformat(),
                    'stats': {
                        'sites': 0,
                        'snapshots': 0,
                        'comparisons': 0
                    }
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
    
    # ========== DELETE - Delete a server ==========
    elif request.method == "DELETE":
        try:
            # Parse JSON data
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.GET.dict()
            
            server_id = data.get('server_id') or data.get('id')
            cascade = data.get('cascade', '').lower() in ['true', '1', 'yes', 'on']
            
            if not server_id:
                return JsonResponse({
                    'status': 'error',
                    'message': 'server_id is required'
                }, status=400)
            
            try:
                server = Server.objects.prefetch_related(
                    'domains__snapshots',
                    'domains__comparisons'
                ).get(id=server_id)
            except Server.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Server with id {server_id} not found'
                }, status=404)
            
            # Get related data counts
            sites = server.domains.all()
            sites_count = sites.count()
            
            if sites_count > 0:
                snapshots_count = SiteSnapshot.objects.filter(site__in=sites).count()
                comparisons_count = ScreenshotComparison.objects.filter(site__in=sites).count()
                
                # If cascade is False, return error with details
                if not cascade:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Cannot delete server with {sites_count} site(s)',
                        'details': {
                            'sites': sites_count,
                            'snapshots': snapshots_count,
                            'comparisons': comparisons_count,
                            'sites_list': [
                                {'id': s.id, 'name': s.name} 
                                for s in sites[:10]
                            ]
                        },
                        'solution': 'Set "cascade": true to delete all related data'
                    }, status=400)
                
                # CASCADE DELETE
                with transaction.atomic():
                    site_ids = list(sites.values_list('id', flat=True))
                    
                    comparisons_deleted = ScreenshotComparison.objects.filter(site__in=sites).delete()[0]
                    snapshots_deleted = SiteSnapshot.objects.filter(site__in=sites).delete()[0]
                    sites_deleted = sites.delete()[0]
                    
                    server_name = server.name
                    server.delete()
                
                return JsonResponse({
                    'status': 'success',
                    'message': f'Server "{server_name}" and all related data deleted successfully',
                    'deleted': {
                        'server': server_name,
                        'sites': sites_deleted,
                        'snapshots': snapshots_deleted,
                        'comparisons': comparisons_deleted,
                        'site_ids': site_ids[:10]
                    }
                })
            else:
                # No sites, just delete the server
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