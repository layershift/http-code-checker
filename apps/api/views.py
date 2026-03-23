from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.http import JsonResponse
from django_rq import get_queue
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db import transaction
from django.db.models import Count
from apps.monitoring.models import Server, Site, SiteSnapshot, ScreenshotComparison
from apps.monitoring.tasks import monitor_site_score_task, capture_screenshot_task, create_comparison_task
import json
import ipaddress
import inspect
from apps.core.decorators.decorators import ip_allow
import threading
import time
from apps.monitoring.utils import Notify
from datetime import datetime
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample, OpenApiResponse
from drf_spectacular.types import OpenApiTypes
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from apps.api.serializers import ServerSerializer
import os
from rest_framework.decorators import api_view
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema
from django.http import HttpResponse, HttpResponseNotFound
from django.conf import settings
from rest_framework import status



def get_client_ip(request):
    """Extract client IP from request"""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def get_caller_info():
    """Determine what called the function"""
    for frame in inspect.stack():
        if 'handle_sites' in frame.function:
            return 'api'
        elif 'admin.py' in frame.filename:
            return 'admin'
        elif 'loaddata' in frame.function:
            return 'management'
    return 'unknown'

@extend_schema(
    methods=['GET'],
    description="List all servers with statistics",
    summary="List Servers",
    responses={
        200: OpenApiResponse(
            description="Successful response with list of servers",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'servers': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {'type': 'integer'},
                                'name': {'type': 'string'},
                                'description': {'type': 'string'},
                                'ip_address': {'type': 'string'},
                                'ip_version': {'type': 'integer', 'nullable': True},
                                'created_at': {'type': 'string', 'format': 'date-time'},
                                'stats': {
                                    'type': 'object',
                                    'properties': {
                                        'sites': {'type': 'integer'},
                                        'snapshots': {'type': 'integer'},
                                        'comparisons': {'type': 'integer'}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        ),
    },
    tags=['servers'],
)
@extend_schema(
    methods=['POST'],
    description="Create a new server. If IP address is not provided, the requester's IP will be used.",
    summary="Create Server",
    request={
        'application/json': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Server name', 'example': 'Web Server 1'},
                'description': {'type': 'string', 'description': 'Server description', 'example': 'Main web server'},
                'ip_address': {'type': 'string', 'description': 'IPv4 address', 'example': '192.168.1.100'},
            },
            'required': ['name'],
        }
    },
    responses={
        201: OpenApiResponse(
            description="Server created successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string'},
                    'server': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'name': {'type': 'string'},
                            'description': {'type': 'string'},
                            'ip_address': {'type': 'string'},
                            'ip_version': {'type': 'integer'},
                            'ip_source': {'type': 'string', 'enum': ['payload', 'requester']},
                            'created_at': {'type': 'string', 'format': 'date-time'},
                            'stats': {
                                'type': 'object',
                                'properties': {
                                    'sites': {'type': 'integer'},
                                    'snapshots': {'type': 'integer'},
                                    'comparisons': {'type': 'integer'}
                                }
                            }
                        }
                    }
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - missing name or invalid IP"),
    },
    examples=[
        OpenApiExample(
            'Create Server with IP',
            value={'name': 'Web Server 1', 'description': 'Main web server', 'ip_address': '192.168.1.100'},
            request_only=True,
        ),
        OpenApiExample(
            'Create Server without IP (uses requester IP)',
            value={'name': 'Web Server 1', 'description': 'Main web server'},
            request_only=True,
        ),
    ],
    tags=['servers'],
)
@extend_schema(
    methods=['DELETE'],
    description="Delete a server with optional cascade to delete all related sites and snapshots",
    summary="Delete Server",
    request={
        'application/json': {
            'type': 'object',
            'properties': {
                'server_id': {'type': 'integer', 'description': 'Server ID', 'example': 1},
                'cascade': {'type': 'boolean', 'description': 'Delete all related sites and snapshots', 'default': False},
            },
            'required': ['server_id'],
        }
    },
    responses={
        200: OpenApiResponse(
            description="Server deleted successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string'},
                    'deleted': {
                        'type': 'object',
                        'properties': {
                            'server': {'type': 'string'},
                            'sites': {'type': 'integer'},
                            'snapshots': {'type': 'integer'},
                            'comparisons': {'type': 'integer'},
                        }
                    }
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - server has sites and cascade=false"),
        404: OpenApiResponse(description="Server not found"),
    },
    tags=['servers'],
)
@api_view(['GET', "POST", "DELETE"])
@ip_allow(mode='all')
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
            server_ = Server.objects.filter(name=data['name']).first()
            print(server_)
            if server_:
                return JsonResponse({
                'status': 'fail',
                'message': f'Server "{server_.name}" already exist',
                
            }, status=201)
            
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



@extend_schema(
    methods=['GET'],
    description="Get detailed information about a specific site by domain name",
    summary="Get Site Details",
    parameters=[
        OpenApiParameter(
            name='name',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='Site domain name (e.g., example.com)',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="Site details retrieved successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'site': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'name': {'type': 'string'},
                            'server': {
                                'type': 'object',
                                'properties': {
                                    'id': {'type': 'integer'},
                                    'name': {'type': 'string'},
                                    'ip_address': {'type': 'string'},
                                }
                            },
                            'server_ip': {'type': 'string', 'nullable': True},
                            'resolved_ip': {'type': 'string', 'nullable': True},
                            'is_active': {'type': 'boolean'},
                            'created_at': {'type': 'string', 'format': 'date-time'},
                            'stats': {
                                'type': 'object',
                                'properties': {
                                    'total_snapshots': {'type': 'integer'},
                                    'total_comparisons': {'type': 'integer'},
                                    'baseline_snapshot_id': {'type': 'integer', 'nullable': True},
                                }
                            },
                            'snapshots': {
                                'type': 'array',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'id': {'type': 'integer'},
                                        'taken_at': {'type': 'string', 'format': 'date-time'},
                                        'http_status_code': {'type': 'integer'},
                                        'content_length': {'type': 'integer'},
                                        'has_screenshot': {'type': 'boolean'},
                                        'is_baseline': {'type': 'boolean'},
                                        'screenshot_url': {'type': 'string', 'nullable': True},
                                    }
                                }
                            },
                            'recent_comparisons': {
                                'type': 'array',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'id': {'type': 'integer'},
                                        'created_at': {'type': 'string', 'format': 'date-time'},
                                        'ssim_score': {'type': 'number'},
                                        'percent_difference': {'type': 'number'},
                                        'changed_pixels': {'type': 'integer'},
                                        'total_pixels': {'type': 'integer'},
                                        'previous_snapshot_id': {'type': 'integer'},
                                        'current_snapshot_id': {'type': 'integer'},
                                        'has_heatmap': {'type': 'boolean'},
                                        'has_diff': {'type': 'boolean'},
                                    }
                                }
                            },
                        }
                    }
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - missing site name"),
        404: OpenApiResponse(description="Site not found"),
    },
    tags=['sites'],
)
@extend_schema(
    methods=['POST'],
    description="Create one or more sites. Server can be assigned by name or auto-detected by IP.",
    summary="Create Site(s)",
    request={
        'application/json': {
            'oneOf': [
                {
                    'type': 'object',
                    'properties': {
                        'name': {'type': 'string', 'description': 'Single site name', 'example': 'example.com'},
                    },
                    'required': ['name'],
                },
                {
                    'type': 'object',
                    'properties': {
                        'names': {
                            'type': 'array',
                            'items': {'type': 'string'},
                            'description': 'List of site names',
                            'example': ['example.com', 'google.com', 'github.com'],
                        },
                    },
                    'required': ['names'],
                }
            ],
            'properties': {
                'server_name': {'type': 'string', 'description': 'Server name to associate'},
                'is_active': {'type': 'boolean', 'description': 'Site active status', 'default': True},
            },
        }
    },
    responses={
        201: OpenApiResponse(
            description="Sites created successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string'},
                    'results': {
                        'type': 'object',
                        'properties': {
                            'created': {
                                'type': 'array',
                                'items': {
                                    'type': 'object',
                                    'properties': {
                                        'id': {'type': 'integer'},
                                        'name': {'type': 'string'},
                                        'snapshot_id': {'type': 'integer'},
                                        'job_id': {'type': 'string'},
                                        'server': {'type': 'string', 'nullable': True},
                                        'server_assigned_by': {'type': 'string', 'enum': ['name', 'ip', 'none']},
                                    }
                                }
                            },
                            'skipped': {'type': 'array'},
                            'failed': {'type': 'array'},
                        }
                    },
                    'stats': {
                        'type': 'object',
                        'properties': {
                            'total': {'type': 'integer'},
                            'created': {'type': 'integer'},
                            'skipped': {'type': 'integer'},
                            'failed': {'type': 'integer'},
                        }
                    },
                    'warning': {'type': 'string', 'nullable': True},
                    'info': {'type': 'string', 'nullable': True},
                }
            }
        ),
        400: OpenApiResponse(description="Bad request"),
        404: OpenApiResponse(description="Server not found"),
    },
    examples=[
        OpenApiExample(
            'Create Single Site',
            value={'name': 'example.com', 'server_name': 'Web Server 1'},
            request_only=True,
        ),
        OpenApiExample(
            'Create Multiple Sites',
            value={'names': ['example.com', 'google.com'], 'server_name': 'Web Server 1'},
            request_only=True,
        ),
    ],
    tags=['sites'],
)
@extend_schema(
    methods=['PATCH'],
    description="Update a site's server, active status, or IP configuration",
    summary="Update Site",
    request={
        'application/json': {
            'type': 'object',
            'properties': {
                'site_id': {'type': 'integer', 'description': 'Site ID'},
                'name': {'type': 'string', 'description': 'Site name (alternative to ID)'},
                'server_name': {'type': 'string', 'description': 'New server name (use null to remove)', 'nullable': True},
                'is_active': {'type': 'boolean', 'description': 'Update active status'},
                'server_ip': {'type': 'string', 'description': 'Update server IP'},
            },
        }
    },
    responses={
        200: OpenApiResponse(
            description="Site updated successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string'},
                    'site': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'name': {'type': 'string'},
                            'server': {'type': 'string', 'nullable': True},
                            'server_id': {'type': 'integer', 'nullable': True},
                            'server_ip': {'type': 'string', 'nullable': True},
                            'is_active': {'type': 'boolean'},
                        }
                    },
                    'updates': {'type': 'object'},
                }
            }
        ),
        400: OpenApiResponse(description="Bad request"),
        404: OpenApiResponse(description="Site or server not found"),
    },
    tags=['sites'],
)
@extend_schema(
    methods=['DELETE'],
    description="Delete a site with optional cascade to delete all snapshots and comparisons",
    summary="Delete Site",
    request={
        'application/json': {
            'type': 'object',
            'properties': {
                'site_id': {'type': 'integer', 'description': 'Site ID'},
                'name': {'type': 'string', 'description': 'Site name (alternative to ID)'},
                'cascade': {'type': 'boolean', 'description': 'Delete all snapshots and comparisons', 'default': False},
            },
        }
    },
    responses={
        200: OpenApiResponse(
            description="Site deleted successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string'},
                    'deleted': {
                        'type': 'object',
                        'properties': {
                            'site': {'type': 'string'},
                            'snapshots': {'type': 'integer'},
                            'comparisons': {'type': 'integer'},
                        }
                    },
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - site has snapshots and cascade=false"),
        404: OpenApiResponse(description="Site not found"),
    },
    tags=['sites'],
)
@api_view(['GET', "POST", "DELETE", "PATCH"])
@ip_allow(mode='all')
@csrf_exempt
@require_http_methods(["GET", "POST", "DELETE", "PATCH"])
def handle_sites(request):
    """
    API endpoint for site management
    GET: Get site details by name (query param: ?name=example.com)
    POST: Create multiple sites (accepts list of names)
    PATCH: Update site (set/change server)
    DELETE: Delete a site with optional cascade
    """
    
    # ========== GET - Get site by name ==========
    if request.method == "GET":
        site_name = request.GET.get('name')
        
        if not site_name:
            return JsonResponse({
                'status': 'error',
                'message': 'Site name is required (use ?name=example.com)'
            }, status=400)
        
        try:
            site = Site.objects.get(name=site_name)
        except Site.DoesNotExist:
            return JsonResponse({
                'status': 'error',
                'message': f'Site "{site_name}" not found'
            }, status=404)
        
        # Get server info
        server_info = None
        if site.server:
            server_info = {
                'id': site.server.id,
                'name': site.server.name,
                'ip_address': site.server.ip_address
            }
        
        # Get snapshots
        snapshots = site.snapshots.all().order_by('-taken_at')
        snapshots_data = []
        baseline_id = None
        
        for snapshot in snapshots:
            snapshot_data = {
                'id': snapshot.id,
                'taken_at': snapshot.taken_at.isoformat(),
                'http_status_code': snapshot.http_status_code,
                'content_length': snapshot.content_length,
                'has_screenshot': bool(snapshot.screenshot),
                'is_baseline': snapshot.is_baseline
            }
            
            if snapshot.screenshot:
                snapshot_data['screenshot_url'] = snapshot.screenshot.url
            
            if snapshot.is_baseline:
                baseline_id = snapshot.id
            
            snapshots_data.append(snapshot_data)
        
        # Get comparisons
        comparisons = ScreenshotComparison.objects.filter(
            Q(previous_snapshot__site=site) | Q(current_snapshot__site=site)
        ).order_by('-created_at')[:10]
        
        comparisons_data = []
        for comp in comparisons:
            comparisons_data.append({
                'id': comp.id,
                'created_at': comp.created_at.isoformat(),
                'ssim_score': comp.ssim_score,
                'percent_difference': comp.percent_difference,
                'changed_pixels': comp.changed_pixels,
                'total_pixels': comp.total_pixels,
                'previous_snapshot_id': comp.previous_snapshot.id,
                'current_snapshot_id': comp.current_snapshot.id,
                'has_heatmap': bool(comp.heatmap),
                'has_diff': bool(comp.diff_image)
            })
        
        return JsonResponse({
            'status': 'success',
            'site': {
                'id': site.id,
                'name': site.name,
                'server': server_info,
                'server_ip': site.server_ip,
                'resolved_ip': site.resolved_ip,
                'is_active': site.is_active,
                'created_at': site.created_at.isoformat(),
                'stats': {
                    'total_snapshots': snapshots.count(),
                    'total_comparisons': comparisons.count(),
                    'baseline_snapshot_id': baseline_id
                },
                'snapshots': snapshots_data,
                'recent_comparisons': comparisons_data
            }
        })
    
    # ========== POST - Create multiple sites ==========
    elif request.method == "POST":
        try:
            # Parse JSON data
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST.dict()
            
            # Check if we have names list or single name
            names = data.get('names')
            single_name = data.get('name')
            
            if not names and not single_name:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Either "name" (single) or "names" (list) is required'
                }, status=400)
            
            # Convert single name to list for uniform processing
            if single_name:
                names = [single_name]
            
            # Validate names is a list
            if not isinstance(names, list):
                return JsonResponse({
                    'status': 'error',
                    'message': '"names" must be a list'
                }, status=400)
            
            if len(names) == 0:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Names list cannot be empty'
                }, status=400)
            
            # Determine caller for logging
            caller = get_caller_info()
            
            # Determine server
            server = None
            server_name = data.get('server_name') or data.get('server')
            server_found_by_ip = False
            client_ip = get_client_ip(request)
            
            if server_name:
                # Try to find server by name
                try:
                    server = Server.objects.get(name=server_name)
                    print(f"✅ Found server by name: {server_name}")
                except Server.DoesNotExist:
                    return JsonResponse({
                        'status': 'error',
                        'message': f'Server with name "{server_name}" not found'
                    }, status=400)
            else:
                # No server name provided, try to find by client IP
                print(f"🔍 No server name provided, checking IP: {client_ip}")
                
                # Look for server with matching IP
                server = Server.objects.filter(ip_address=client_ip).first()
                if server:
                    print(f"✅ Found server by IP: {server.name} ({client_ip})")
                    server_found_by_ip = True
                else:
                    print(f"ℹ️ No server found with IP {client_ip}, creating sites without server")
            
            results = {
                'created': [],
                'failed': [],
                'skipped': []
            }
            
            # Process each site name
            with transaction.atomic():
                for site_name in names:
                    site_name = site_name.lower().strip()
                    
                    # Check if site already exists
                    existing_site = Site.objects.filter(name=site_name).first()
                    if existing_site:
                        results['skipped'].append({
                            'name': site_name,
                            'reason': 'already_exists',
                            'existing_id': existing_site.id
                        })
                        continue
                    
                    try:
                        # Create site
                        site = Site.objects.create(
                            name=site_name,
                            server=server,
                            server_ip=data.get('server_ip'),
                            is_active=data.get('is_active', True)
                        )
                        
                        # Try to resolve IP (optional, can fail gracefully)
                        try:
                            resolved = site.resolve_ip()
                            if resolved:
                                site.resolved_ip = resolved
                                site.save(update_fields=['resolved_ip'])
                        except:
                            pass
                        
                        # Create initial snapshot (will be baseline)
                        snapshot = SiteSnapshot.objects.create(
                            site=site,
                            http_status_code=0,
                            content_length=0,
                            is_baseline=True
                        )
                        
                        # Enqueue screenshot task via RQ
                        queue = get_queue('default')
                        screenshot_job = queue.enqueue(
                            capture_screenshot_task,
                            snapshot.id,
                            site.name,
                            site.id
                        )
                        
                        results['created'].append({
                            'id': site.id,
                            'name': site.name,
                            'snapshot_id': snapshot.id,
                            'job_id': screenshot_job.id,
                            'server': server.name if server else None,
                            'server_assigned_by': 'name' if server_name else ('ip' if server_found_by_ip else 'none')
                        })
                        
                        print(f"✅ Created site: {site.name} (ID: {site.id})")
                        
                    except Exception as e:
                        results['failed'].append({
                            'name': site_name,
                            'error': str(e)
                        })
            
            response_data = {
                'status': 'success',
                'message': f"Processed {len(names)} site(s)",
                'results': results,
                'stats': {
                    'total': len(names),
                    'created': len(results['created']),
                    'skipped': len(results['skipped']),
                    'failed': len(results['failed'])
                }
            }
            
            # Add info about server auto-detection
            if not server_name and not server:
                response_data['warning'] = f'No server found with IP {client_ip}. Sites created without server.'
            elif not server_name and server:
                response_data['info'] = f'Server "{server.name}" auto-assigned based on your IP ({server.ip_address})'
            
            # Add caller info
            response_data['caller'] = caller
            
            return JsonResponse(response_data, status=201)
            
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
    
    # ========== PATCH - Update site ==========
    elif request.method == "PATCH":
        try:
            # Parse JSON data
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.POST.dict()
            
            # Need either site_id or site_name
            site_id = data.get('site_id')
            site_name = data.get('name')
            
            if not site_id and not site_name:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Either site_id or site_name is required'
                }, status=400)
            
            # Find the site
            try:
                if site_id:
                    site = Site.objects.get(id=site_id)
                else:
                    site = Site.objects.get(name=site_name)
            except Site.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Site not found'
                }, status=404)
            
            updates = {}
            
            # Update server if provided
            server_name = data.get('server_name') or data.get('server')
            if server_name is not None:
                if server_name == '' or server_name.lower() == 'null':
                    # Remove server association
                    site.server = None
                    updates['server'] = 'removed'
                    print(f"🔄 Removed server from site {site.name}")
                else:
                    # Find server by name
                    try:
                        server = Server.objects.get(name=server_name)
                        site.server = server
                        updates['server'] = {
                            'id': server.id,
                            'name': server.name,
                            'ip_address': server.ip_address
                        }
                        print(f"🔄 Updated server for site {site.name} to {server.name}")
                    except Server.DoesNotExist:
                        return JsonResponse({
                            'status': 'error',
                            'message': f'Server with name "{server_name}" not found'
                        }, status=400)
            
            # Update other fields if provided
            if 'is_active' in data:
                site.is_active = data['is_active']
                updates['is_active'] = site.is_active
            
            if 'server_ip' in data:
                site.server_ip = data['server_ip']
                updates['server_ip'] = site.server_ip
            
            # Save changes
            if updates:
                site.save()
            
            return JsonResponse({
                'status': 'success',
                'message': f'Site "{site.name}" updated successfully',
                'site': {
                    'id': site.id,
                    'name': site.name,
                    'server': site.server.name if site.server else None,
                    'server_id': site.server.id if site.server else None,
                    'server_ip': site.server_ip,
                    'is_active': site.is_active
                },
                'updates': updates
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
    
    # ========== DELETE - Delete site ==========
    elif request.method == "DELETE":
        try:
            # Parse JSON data
            if request.content_type == 'application/json':
                data = json.loads(request.body)
            else:
                data = request.GET.dict()
            
            site_id = data.get('site_id') or data.get('id')
            site_name = data.get('name')
            cascade = data.get('cascade', '').lower() in ['true', '1', 'yes', 'on']
            
            if not site_id and not site_name:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Either site_id or site_name is required'
                }, status=400)
            
            # Find the site
            try:
                if site_id:
                    site = Site.objects.get(id=site_id)
                else:
                    site = Site.objects.get(name=site_name)
            except Site.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Site not found'
                }, status=404)
            
            # Get related data counts
            snapshots_count = site.snapshots.count()
            comparisons_count = ScreenshotComparison.objects.filter(
                Q(previous_snapshot__site=site) | Q(current_snapshot__site=site)
            ).count()
            
            # If not cascade and has related data, return error with details
            if (snapshots_count > 0 or comparisons_count > 0) and not cascade:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Cannot delete site with {snapshots_count} snapshots and {comparisons_count} comparisons',
                    'details': {
                        'snapshots': snapshots_count,
                        'comparisons': comparisons_count
                    },
                    'solution': 'Set "cascade": true to delete all related data'
                }, status=400)
            
            # CASCADE DELETE
            with transaction.atomic():
                site_name_deleted = site.name
                
                # Delete comparisons first (due to FK constraints)
                if comparisons_count > 0:
                    ScreenshotComparison.objects.filter(
                        Q(previous_snapshot__site=site) | Q(current_snapshot__site=site)
                    ).delete()
                
                # Delete snapshots (django-cleanup will handle files)
                if snapshots_count > 0:
                    site.snapshots.all().delete()
                
                # Delete the site
                site.delete()
            
            return JsonResponse({
                'status': 'success',
                'message': f'Site "{site_name_deleted}" deleted successfully',
                'deleted': {
                    'site': site_name_deleted,
                    'snapshots': snapshots_count,
                    'comparisons': comparisons_count
                }
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
        
@extend_schema(
    description="List all snapshots for a site",
    parameters=[
        OpenApiParameter(
            name='site_name',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description='Site name (domain)',
            required=True,
        ),
        OpenApiParameter(
            name='limit',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Number of snapshots to return',
            required=False,
            default=20,
        ),
        OpenApiParameter(
            name='offset',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Pagination offset',
            required=False,
            default=0,
        ),
    ],
    responses={200: dict, 404: dict},
    tags=['api_list_snapshots'],
)


@extend_schema(
    description="List all snapshots for a specific site with pagination",
    summary="List Site Snapshots",
    parameters=[
        OpenApiParameter(
            name='site_name',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description='Site domain name (e.g., example.com)',
            required=True,
        ),
        OpenApiParameter(
            name='limit',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Number of snapshots to return',
            required=False,
            default=20,
        ),
        OpenApiParameter(
            name='offset',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Pagination offset',
            required=False,
            default=0,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="List of snapshots retrieved successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'site': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'name': {'type': 'string'},
                            'baseline_snapshot_id': {'type': 'integer', 'nullable': True},
                            'baseline_taken_at': {'type': 'string', 'format': 'date-time', 'nullable': True},
                        }
                    },
                    'snapshots': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {'type': 'integer'},
                                'taken_at': {'type': 'string', 'format': 'date-time'},
                                'taken_at_timestamp': {'type': 'number'},
                                'http_status_code': {'type': 'integer'},
                                'content_length': {'type': 'integer'},
                                'has_screenshot': {'type': 'boolean'},
                                'is_baseline': {'type': 'boolean'},
                                'screenshot_url': {'type': 'string', 'nullable': True},
                            }
                        }
                    },
                    'pagination': {
                        'type': 'object',
                        'properties': {
                            'total': {'type': 'integer'},
                            'limit': {'type': 'integer'},
                            'offset': {'type': 'integer'},
                            'returned': {'type': 'integer'},
                            'has_next': {'type': 'boolean'},
                        }
                    },
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - missing site name"),
        404: OpenApiResponse(description="Site not found"),
    },
    tags=['snapshots'],
)
@api_view(['GET'])
@ip_allow(mode='all')
@csrf_exempt
@require_http_methods(["GET"])
def list_snapshots(request, site_name=None):
    # Your existing code
    pass


@extend_schema(
    description="Get status of a specific snapshot",
    parameters=[
        OpenApiParameter(
            name='snapshot_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            description='Snapshot ID',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(description="Snapshot status retrieved"),
        404: OpenApiResponse(description="Snapshot not found"),
    },
    tags=['snapshots'],
)
@api_view(['GET'])
@ip_allow(mode='all')
@csrf_exempt
@require_http_methods(["GET"])
def get_snapshot_status(request, snapshot_id):
    # Your existing code
    pass


@extend_schema(
    methods=['GET'],
    description="List all snapshots for a specific site with pagination",
    summary="List Site Snapshots",
    parameters=[
        OpenApiParameter(
            name='site_name',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description='Site domain name (e.g., example.com)',
            required=True,
        ),
        OpenApiParameter(
            name='limit',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Number of snapshots to return',
            required=False,
            default=20,
        ),
        OpenApiParameter(
            name='offset',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Pagination offset',
            required=False,
            default=0,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="List of snapshots retrieved successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'site': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'name': {'type': 'string'},
                            'baseline_snapshot_id': {'type': 'integer', 'nullable': True},
                            'baseline_taken_at': {'type': 'string', 'format': 'date-time', 'nullable': True},
                        }
                    },
                    'snapshots': {
                        'type': 'array',
                        'items': {
                            'type': 'object',
                            'properties': {
                                'id': {'type': 'integer'},
                                'taken_at': {'type': 'string', 'format': 'date-time'},
                                'taken_at_timestamp': {'type': 'number'},
                                'http_status_code': {'type': 'integer'},
                                'content_length': {'type': 'integer'},
                                'has_screenshot': {'type': 'boolean'},
                                'is_baseline': {'type': 'boolean'},
                                'screenshot_url': {'type': 'string', 'nullable': True},
                            }
                        }
                    },
                    'pagination': {
                        'type': 'object',
                        'properties': {
                            'total': {'type': 'integer'},
                            'limit': {'type': 'integer'},
                            'offset': {'type': 'integer'},
                            'returned': {'type': 'integer'},
                            'has_next': {'type': 'boolean'},
                        }
                    },
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - missing site name"),
        404: OpenApiResponse(description="Site not found"),
    },
    tags=['snapshots'],
)


@extend_schema(
    methods=['GET'],
    description="Get detailed status of a specific snapshot by ID",
    summary="Get Snapshot Status",
    parameters=[
        OpenApiParameter(
            name='snapshot_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            description='Snapshot ID',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="Snapshot status retrieved successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'snapshot': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'site': {'type': 'string'},
                            'taken_at': {'type': 'string', 'format': 'date-time'},
                            'http_status_code': {'type': 'integer'},
                            'content_length': {'type': 'integer'},
                            'has_screenshot': {'type': 'boolean'},
                            'is_baseline': {'type': 'boolean'},
                            'screenshot_url': {'type': 'string', 'nullable': True},
                        }
                    },
                    'comparison': {
                        'type': 'object',
                        'nullable': True,
                        'properties': {
                            'id': {'type': 'integer'},
                            'ssim_score': {'type': 'number'},
                            'percent_difference': {'type': 'number'},
                            'created_at': {'type': 'string', 'format': 'date-time'},
                            'heatmap_url': {'type': 'string', 'nullable': True},
                            'diff_url': {'type': 'string', 'nullable': True},
                        }
                    },
                }
            }
        ),
        404: OpenApiResponse(description="Snapshot not found"),
    },
    tags=['snapshots'],
)


@extend_schema(
    methods=['POST'],
    description="Trigger a new screenshot snapshot for a site. Optionally set it as the new baseline.",
    summary="Trigger Snapshot",
    request={
        'application/json': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string', 'description': 'Site name', 'example': 'example.com'},
                'site_name': {'type': 'string', 'description': 'Site name (alternative)'},
                'domain': {'type': 'string', 'description': 'Domain name (alternative)'},
                'set_as_baseline': {'type': 'boolean', 'description': 'Set this snapshot as new baseline', 'default': False},
            },
            'required': ['name'],
        }
    },
    responses={
        202: OpenApiResponse(
            description="Snapshot triggered successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string'},
                    'snapshot': {
                        'type': 'object',
                        'properties': {
                            'id': {'type': 'integer'},
                            'created_at': {'type': 'string', 'format': 'date-time'},
                            'status': {'type': 'string', 'enum': ['queued']},
                            'is_baseline': {'type': 'boolean'},
                        }
                    },
                    'jobs': {
                        'type': 'object',
                        'properties': {
                            'screenshot': {
                                'type': 'object',
                                'properties': {
                                    'id': {'type': 'string'},
                                    'status': {'type': 'string', 'enum': ['queued']},
                                }
                            }
                        }
                    },
                    'current_baseline': {
                        'type': 'object',
                        'nullable': True,
                        'properties': {
                            'id': {'type': 'integer'},
                            'taken_at': {'type': 'string', 'format': 'date-time'},
                        }
                    },
                    'baseline_change': {
                        'type': 'object',
                        'nullable': True,
                        'properties': {
                            'previous_baseline_id': {'type': 'integer', 'nullable': True},
                            'previous_baseline_taken_at': {'type': 'string', 'format': 'date-time', 'nullable': True},
                            'new_baseline_id': {'type': 'integer'},
                            'new_baseline_taken_at': {'type': 'string', 'format': 'date-time'},
                        }
                    },
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - missing site name or inactive site"),
        404: OpenApiResponse(description="Site not found"),
    },
    examples=[
        OpenApiExample(
            'Trigger Regular Snapshot',
            value={'name': 'example.com'},
            request_only=True,
        ),
        OpenApiExample(
            'Trigger Baseline Snapshot',
            value={'name': 'example.com', 'set_as_baseline': True},
            request_only=True,
        ),
    ],
    tags=['snapshots'],
)

@api_view(['POST', 'GET'])
@ip_allow(mode='all')
@csrf_exempt
@require_http_methods(["POST", "GET"])
def trigger_snapshot(request):
    """
    API endpoint to trigger a new snapshot for a site
    POST: {"name": "example.com"} or {"name": "example.com", "set_as_baseline": true}
    """
    try:
        # With @api_view, request.data is already parsed - use it directly
        data = request.data
        
        site_name = data.get('name') or data.get('site_name') or data.get('domain')
        
        if not site_name:
            return Response({
                'status': 'error',
                'message': 'Site name is required (use "name": "example.com")'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        set_as_baseline = data.get('set_as_baseline', False)
        
        # Find the site
        from apps.monitoring.models import Site
        try:
            site = Site.objects.get(name=site_name.lower().strip())
        except Site.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Site "{site_name}" not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Check if site is active
        if not site.is_active:
            return Response({
                'status': 'error',
                'message': f'Site "{site_name}" is inactive'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get current baseline snapshot for reference
        current_baseline = site.snapshots.filter(is_baseline=True).first()
        
        # Create new snapshot
        from django.db import transaction
        from apps.monitoring.models import SiteSnapshot
        from django_rq import get_queue
        from apps.monitoring.tasks import capture_screenshot_task
        
        with transaction.atomic():
            if set_as_baseline:
                site.snapshots.filter(is_baseline=True).update(is_baseline=False)
                snapshot = SiteSnapshot.objects.create(
                    site=site,
                    http_status_code=0,
                    content_length=0,
                    is_baseline=True
                )
                baseline_message = " and set as new baseline"
            else:
                snapshot = SiteSnapshot.objects.create(
                    site=site,
                    http_status_code=0,
                    content_length=0
                )
                baseline_message = ""
            
            queue = get_queue('default')
            screenshot_job = queue.enqueue(
                capture_screenshot_task,
                snapshot.id,
                site.name,
                site.id
            )
        
        response_data = {
            'status': 'success',
            'message': f'Snapshot triggered for "{site.name}"{baseline_message}',
            'snapshot': {
                'id': snapshot.id,
                'created_at': snapshot.taken_at.isoformat(),
                'status': 'queued',
                'is_baseline': snapshot.is_baseline
            },
            'jobs': {
                'screenshot': {
                    'id': screenshot_job.id,
                    'status': 'queued'
                }
            }
        }
        
        if set_as_baseline:
            response_data['baseline_change'] = {
                'previous_baseline_id': current_baseline.id if current_baseline else None,
                'previous_baseline_taken_at': current_baseline.taken_at.isoformat() if current_baseline else None,
                'new_baseline_id': snapshot.id,
                'new_baseline_taken_at': snapshot.taken_at.isoformat()
            }
        else:
            if current_baseline:
                response_data['current_baseline'] = {
                    'id': current_baseline.id,
                    'taken_at': current_baseline.taken_at.isoformat()
                }
        
        return Response(response_data, status=status.HTTP_202_ACCEPTED)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@ip_allow(mode='all')
@csrf_exempt
@require_http_methods(["GET"])
def get_snapshot_status(request, snapshot_id):
    """
    API endpoint to check the status of a snapshot
    GET /api/v1/snapshots/123/status/
    """
    try:
        snapshot = SiteSnapshot.objects.select_related('site').get(id=snapshot_id)
        
        # Check if there are any comparisons involving this snapshot
        from apps.monitoring.models import ScreenshotComparison
        
        as_previous = ScreenshotComparison.objects.filter(previous_snapshot=snapshot).first()
        as_current = ScreenshotComparison.objects.filter(current_snapshot=snapshot).first()
        
        comparison = as_previous or as_current
        
        response = {
            'status': 'success',
            'snapshot': {
                'id': snapshot.id,
                'site': snapshot.site.name,
                'taken_at': snapshot.taken_at.isoformat(),
                'http_status_code': snapshot.http_status_code,
                'content_length': snapshot.content_length,
                'has_screenshot': bool(snapshot.screenshot),
                'is_baseline': snapshot.is_baseline
            }
        }
        
        if snapshot.screenshot:
            response['snapshot']['screenshot_url'] = snapshot.screenshot.url
        
        if comparison:
            response['comparison'] = {
                'id': comparison.id,
                'ssim_score': comparison.ssim_score,
                'percent_difference': comparison.percent_difference,
                'created_at': comparison.created_at.isoformat()
            }
            if comparison.heatmap:
                response['comparison']['heatmap_url'] = comparison.heatmap.url
            if comparison.diff_image:
                response['comparison']['diff_url'] = comparison.diff_image.url
        
        return JsonResponse(response)
        
    except SiteSnapshot.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': f'Snapshot {snapshot_id} not found'
        }, status=404)
    except Exception as e:
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)

@extend_schema(
    description="List all snapshots for a site",
    parameters=[
        OpenApiParameter(
            name='site_name',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description='Site name (domain)',
            required=True,
        ),
        OpenApiParameter(
            name='limit',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Number of snapshots to return',
            required=False,
            default=20,
        ),
        OpenApiParameter(
            name='offset',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.QUERY,
            description='Pagination offset',
            required=False,
            default=0,
        ),
    ],
    responses={200: dict, 404: dict},
    tags=['api_list_snapshots'],
)
@ip_allow(mode='all')
@csrf_exempt
@require_http_methods(["GET"])
@api_view(['GET'])
def list_snapshots(request, site_name=None):
    """
    API endpoint to list all snapshots for a site
    GET /api/v1/snapshots/example.com/
    Ordered by taken_at DESC (newest first)
    """
    if not site_name:
        return JsonResponse({
            'status': 'error',
            'message': 'Site name is required in URL'
        }, status=400)
    
    try:
        site = Site.objects.get(name=site_name.lower().strip())
    except Site.DoesNotExist:
        return JsonResponse({
            'status': 'error',
            'message': f'Site "{site_name}" not found'
        }, status=404)
    
    # Get all snapshots for the site - newest first
    snapshots = site.snapshots.all().order_by('-taken_at')
    
    # Pagination
    limit = request.GET.get('limit', 20)
    offset = request.GET.get('offset', 0)
    
    try:
        limit = int(limit)
        offset = int(offset)
    except:
        limit = 20
        offset = 0
    
    paginated = snapshots[offset:offset + limit]
    
    snapshots_data = []
    for snap in paginated:
        data = {
            'id': snap.id,
            'taken_at': snap.taken_at.isoformat(),
            'taken_at_timestamp': snap.taken_at.timestamp(),
            'http_status_code': snap.http_status_code,
            'content_length': snap.content_length,
            'has_screenshot': bool(snap.screenshot),
            'is_baseline': snap.is_baseline
        }
        if snap.screenshot:
            data['screenshot_url'] = snap.screenshot.url
        snapshots_data.append(data)
    
    # Get baseline info
    baseline = site.snapshots.filter(is_baseline=True).first()
    
    return JsonResponse({
        'status': 'success',
        'site': {
            'id': site.id,
            'name': site.name,
            'baseline_snapshot_id': baseline.id if baseline else None,
            'baseline_taken_at': baseline.taken_at.isoformat() if baseline else None
        },
        'snapshots': snapshots_data,
        'pagination': {
            'total': snapshots.count(),
            'limit': limit,
            'offset': offset,
            'returned': len(snapshots_data),
            'has_next': (offset + limit) < snapshots.count()
        }
    })

@extend_schema(
    methods=['POST'],
    description="Dispatch complete monitoring for a server (all sites) or a specific domain. Runs snapshot, comparison, and site score for each site.",
    summary="Dispatch Complete Monitoring",
    request={
        'application/json': {
            'type': 'object',
            'properties': {
                'server': {'type': 'string', 'description': 'Server name to monitor all sites', 'example': 'Web Server 1'},
                'domain': {'type': 'string', 'description': 'Specific domain to monitor', 'example': 'example.com'},
                'site': {'type': 'string', 'description': 'Alternative for domain'},
                'name': {'type': 'string', 'description': 'Alternative for domain'},
            },
        }
    },
    responses={
        202: OpenApiResponse(
            description="Monitoring dispatched",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string'},
                    'target': {
                        'type': 'object',
                        'properties': {
                            'type': {'type': 'string', 'enum': ['server', 'domain']},
                            'name': {'type': 'string'},
                            'id': {'type': 'integer'},
                            'site_count': {'type': 'integer', 'nullable': True},
                        }
                    },
                    'notification': {'type': 'string'},
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - missing server or domain"),
        404: OpenApiResponse(description="Server or domain not found"),
    },
    examples=[
        OpenApiExample(
            'Monitor Server',
            value={'server': 'Web Server 1'},
            request_only=True,
        ),
        OpenApiExample(
            'Monitor Domain',
            value={'domain': 'example.com'},
            request_only=True,
        ),
    ],
    tags=['monitoring'],
)
@api_view(['POST'])
@ip_allow(mode='all')
@csrf_exempt
@require_http_methods(["POST"])
def dispatch_comparison(request):
    """
    API endpoint to trigger complete monitoring for a server or domain
    POST payload: {"server": "server_name"} or {"domain": "example.com"}
    Runs: snapshot + comparison + site score
    Returns: "Success" via Notify.send() when all jobs complete
    """
    print(f"📡 Received monitoring dispatch request: ")
    if request.method == 'POST' and not hasattr(request, '_body'):
        request.data
    print(f"📡 Received monitoring dispatch request: {request.data}")
    try:
        # Parse JSON data
        print(f"📡 Received monitoring dispatch request: {type(request.data)} ")

        if request.content_type == 'application/json' and type(request.data) == bytes:
            data = json.loads(request.data)
        else:
            data = request.data
        
        print(f"📡 Received monitoring dispatch request: {data} xxx")
        ticket_id= data.get('ticket_id')
        server_name = data.get('server')
        domain_name = data.get('domain') or data.get('site') or data.get('name')
        
        if not server_name and not domain_name:
            return JsonResponse({
                'status': 'error',
                'message': 'Either "server" or "domain" is required in payload'
            }, status=400)
        
        results = {
            'target': {},
            'sites': [],
            'start_time': datetime.now().isoformat()
        }
        print(f"🚀 Dispatching monitoring for server: {server_name}, domain: {domain_name}")
        queue = get_queue('default')
        
        # CASE 1: Monitor a specific domain
        if domain_name:
            try:
                site = Site.objects.get(name=domain_name.lower().strip())
                results['target'] = {
                    'type': 'domain',
                    'name': site.name,
                    'id': site.id
                }
                
                # Create and enqueue jobs for this site
                job_ids = enqueue_site_monitoring(site, queue, ticket_id=ticket_id)
                
                results['sites'].append({
                    'name': site.name,
                    'jobs': job_ids
                })
                
            except Site.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Domain "{domain_name}" not found'
                }, status=404)
        
        # CASE 2: Monitor all sites on a server
        elif server_name:
            try:
                server = Server.objects.get(name=server_name)
                sites = server.domains.filter(is_active=True)
                
                results['target'] = {
                    'type': 'server',
                    'name': server.name,
                    'id': server.id,
                    'site_count': sites.count()
                }
                
                for site in sites:
                    job_ids = enqueue_site_monitoring(site, queue, ticket_id=ticket_id)
                    
                    results['sites'].append({
                        'name': site.name,
                        'jobs': job_ids
                    })
                
            except Server.DoesNotExist:
                return JsonResponse({
                    'status': 'error',
                    'message': f'Server "{server_name}" not found'
                }, status=404)
        
        # Start background task to wait for completion and send success notification
        thread = threading.Thread(
            target=wait_for_completion_and_notify,
            args=(results['target'], results['sites'], results['start_time'])
        )
        thread.daemon = True
        thread.start()
        
        # Return immediate response
        response = {
            'status': 'success',
            'message': f'Monitoring dispatched for {len(results["sites"])} site(s)',
            'target': results['target'],
            'notification': 'Success message will be sent when all jobs complete'
        }
        
        return JsonResponse(response, status=202)
        
    except json.JSONDecodeError:
        return JsonResponse({
            'status': 'error',
            'message': 'Invalid JSON data'
        }, status=400)
    except Exception as e:
        print(f"Error in dispatch_comparison: {e}")
        return JsonResponse({
            'status': 'error',
            'message': str(e)
        }, status=500)


def enqueue_site_monitoring(site, queue, ticket_id=None):
    """Helper function to enqueue all monitoring jobs for a site"""
    
    # Create snapshot
    print(f"⏱️ Creating snapshot for site: {site.name} (ticket_id={ticket_id})")
    snapshot = SiteSnapshot.objects.create(
        site=site,
        http_status_code=0,
        content_length=0,
        ticket=ticket_id
    )
    
    # Enqueue screenshot task
    screenshot_job = queue.enqueue(
        capture_screenshot_task,
        snapshot.id,
        site.name,
        site.id
    )
    
    # Enqueue comparison task (depends on screenshot)
    # comparison_job = queue.enqueue(
    #     create_comparison_task,
    #     snapshot.id,
    #     site.id,
    #     depends_on=screenshot_job
    # )
    
    # Enqueue score task (depends on screenshot)
    # score_job = queue.enqueue(
    #     monitor_site_score_task,
    #     site.id,
    #     depends_on=comparison_job
    # )
    
    return {
        'screenshot': screenshot_job.id,
        # 'comparison': comparison_job.id,
        # 'score': score_job.id
    }


def wait_for_completion_and_notify(target, sites_data, start_time):
    """Wait for all jobs to complete, then send monitoring results via Notify.send()"""
    from rq.job import Job
    from django_rq import get_queue
    from datetime import datetime
    import time
    from apps.monitoring.util.evaluator import SiteEvaluator
    
    queue = get_queue('default')
    connection = queue.connection
    
    # Collect all job IDs
    all_job_ids = []
    site_job_map = {} 

    for site in sites_data:
        site_name = site['name']
        for job_type, job_id in site['jobs'].items():
            all_job_ids.append(job_id)
            site_job_map[job_id] = {
                'site': site_name,
                'type': job_type
            }

    total_jobs = len(all_job_ids)
    print(f"⏳ Waiting for {total_jobs} jobs from {len(sites_data)} sites...")

    max_wait = 3600
    waited = 0
    job_statuses = {job_id: 'queued' for job_id in all_job_ids}

    # Track completed sites
    site_completion = {site['name']: {job_type: False for job_type in site['jobs'].keys()} for site in sites_data}

    while waited < max_wait:
        completed_jobs = 0
        
        for job_id in all_job_ids:
            try:
                job = Job.fetch(job_id, connection=connection)
                status = job.get_status()
                job_statuses[job_id] = status.value
                
                if status.value in ['finished', 'failed']:
                    completed_jobs += 1
                    if job_id in site_job_map:
                        site_name = site_job_map[job_id]['site']
                        job_type = site_job_map[job_id]['type']
                        site_completion[site_name][job_type] = True
            except:
                job_statuses[job_id] = 'expired'
                completed_jobs += 1
    
        completed_sites = sum(1 for site, jobs in site_completion.items() 
                            if all(jobs.values()))
    
        print(f"⏳ Progress: {completed_jobs}/{total_jobs} jobs, {completed_sites}/{len(sites_data)} sites complete")
        
        if completed_jobs >= total_jobs:
            break
        
        time.sleep(5)
        waited += 5
    
    # Calculate duration
    end_time = datetime.now()
    duration = (end_time - datetime.fromisoformat(start_time)).total_seconds()
    
    target_name = target.get('name', 'Unknown')
    target_type = target.get('type', 'target')
    
    # Build monitoring results using SiteEvaluator
    monitoring_lines = []
    all_pass = True
    
    for site_info in sites_data:
        site_name = site_info['name']
        evaluator = SiteEvaluator(site_name)
        
        if evaluator.is_valid():
            passed, text = evaluator.get_monitoring_text(compact=False)
            print(f"✅ Evaluated {site_name}: {'PASS' if passed else 'FAIL'} {text}")
            #monitoring_lines.append(text)
            if not passed:
                monitoring_lines.append(text)
                all_pass = False
        else:
            monitoring_lines.append(f"| {site_name} | Error: {evaluator.error}")
            all_pass = False
    
    # Combine all monitoring texts
    if target_type == 'server':
        # For servers, concatenate all texts with newlines
        monitoring_text = "\n".join(monitoring_lines)
    else:
        # For single domain, just use the first text
        print(monitoring_lines)
        if len (monitoring_lines) == 0:
            print("Everything is ok")
            monitoring_text = f"| {target_name} | ✅ | ✅ | ✅ | ✅"
        else:
            monitoring_text = monitoring_lines[0] if monitoring_lines else "No monitoring data available"
    
    # Prepare the full message
    if target_type == 'server':
        header = f"📊 Monitoring Results for Server: {target_name}"
    else:
        header = f"📊 Monitoring Results for Domain: {target_name}"

    status_emoji = "✅" if all_pass else "⚠️"

    full_message = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Sites: {len(sites_data)} | Jobs: {total_jobs} | Duration: {duration:.1f}s\n"
        f"Overall Status: {status_emoji} {'PASS' if all_pass else 'FAIL'}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━"
    )

    if not all_pass:
        table = (
            f"\n\n| Site Name | Status | SSIM | Score | Change \n"
            f"| --- | --- | --- | --- | --- \n"
            f"{monitoring_text}\n"
        )
        full_message += table
        full_message += f"\n\n━━━━━━━━━━━━━━━━━━━━━━━"

    print(f"Site: {site_name}, Ticket ID: --------------------------")
    ticket_id=SiteSnapshot.objects.filter(site__name=site_name).first()
    if ticket_id is not None:
        ticket_id = ticket_id.ticket
    else:
        ticket_id = "No Ticket ID"
    print(f"Site: {site_name}, Ticket ID: {ticket_id} --------------------------")
    # Send notification
    try:
        Notify.send(
            title=f"{ticket_id} {os.getenv('ZULIP_SUBJECT', 'Monitoring Complete :')} {target_name}",
            body=full_message
        )
        print("✅ Monitoring results notification sent")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")
        print(full_message)  # Fallback to print


# Alternative version with compact text
def wait_for_completion_and_notify_compact(target, sites_data, start_time):
    """Wait for all jobs to complete, then send compact monitoring results"""
    from rq.job import Job
    from django_rq import get_queue
    from datetime import datetime
    import time
    from apps.monitoring.util.evaluator import SiteEvaluator
    
    queue = get_queue('default')
    connection = queue.connection
    
    # Collect all job IDs
    all_job_ids = []
    for site in sites_data:
        for job_type, job_id in site['jobs'].items():
            all_job_ids.append(job_id)
    
    total_jobs = len(all_job_ids)
    print(f"⏳ Waiting for {total_jobs} jobs to complete short...")
    
    # Wait for all jobs to complete
    max_wait = 300
    waited = 0
    completed_jobs = 0
    
    while waited < max_wait and completed_jobs < total_jobs:
        completed_jobs = 0
        for job_id in all_job_ids:
            print(f"⏳ Checking job {job_id}...")
            try:
                job = Job.fetch(job_id, connection=connection)
                status = job.get_status()
                if status in ['finished', 'failed']:
                    completed_jobs += 1
            except:
                pass
        
        if completed_jobs < total_jobs:
            time.sleep(5)
            waited += 5
    
    end_time = datetime.now()
    duration = (end_time - datetime.fromisoformat(start_time)).total_seconds()
    
    target_name = target.get('name', 'Unknown')
    target_type = target.get('type', 'target')
    
    # Build monitoring results
    monitoring_lines = []
    all_pass = True
    
    for site_info in sites_data:
        site_name = site_info['name']
        evaluator = SiteEvaluator(site_name)
        
        if evaluator.is_valid():
            passed, text = evaluator.get_monitoring_text(compact=True)
            monitoring_lines.append(text)
            if not passed:
                all_pass = False
        else:
            monitoring_lines.append(f"| {site_name} | ERROR")
            all_pass = False
    
    # Create a single line for servers (comma-separated)
    if target_type == 'server':
        monitoring_text = " | ".join(monitoring_lines)
    else:
        monitoring_text = monitoring_lines[0] if monitoring_lines else "No data"
    
    status_emoji = "✅" if all_pass else "⚠️"
    
    # Compact one-line message for Zulip
    full_message = (
        f"{status_emoji} {target_type.upper()} {target_name} | "
        f"Sites:{len(sites_data)} Jobs:{total_jobs} Dur:{duration:.0f}s | "
        f"{monitoring_text}"
    )
    
    try:
        Notify.send(
            title=f"{os.getenv('ZULIP_SUBJECT', 'Monitoring Complete :')} {target_name}",
            body=full_message
        )
        print("✅ Compact monitoring notification sent")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")
        print(full_message)


@csrf_exempt
@require_http_methods(["GET"])
def serve_bash_script(request, script):
    """
    Serve a bash script by name from query parameter
    Usage: curl -s  https://your-domain.com/api/v1/bash/<script.sh> | bash -s "arg1"
    """
    
    script_name = script.strip()
    if not script_name:
        
        bash_dir = os.path.join(settings.BASE_DIR, 'bash_scripts')
        try:
            scripts = os.listdir(bash_dir)
            script_list = '\n'.join([f"  - {s}" for s in scripts if s.endswith('.sh')])
            return HttpResponse(
                f"Available scripts:\n{script_list}\n\n"
                f"Usage: curl -s  https://your-domain.com/api/v1/bash/<script.sh> | bash -s [args]",
                content_type='text/plain'
            )
        except:
            return HttpResponse("No scripts available", status=404)
    
    # Security: Prevent directory traversal
    if '..' in script_name or script_name.startswith('/'):
        return HttpResponseNotFound("Invalid script name")
    
    
    bash_dir = os.path.join(settings.BASE_DIR, 'bash_scripts')
    file_path = os.path.join(bash_dir, script_name)
    
    # Check if file exists and is within the bash_scripts directory
    if not os.path.exists(file_path) or not os.path.realpath(file_path).startswith(os.path.realpath(bash_dir)):
        return HttpResponseNotFound("Script not found")
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Return as plain text for piping to bash
        response = HttpResponse(content, content_type='text/plain')
        response['Content-Disposition'] = f'inline; filename="{script_name}"'
        return response
        
    except Exception as e:
        return HttpResponse(f"Error reading file: {e}", status=500)


@api_view(['POST'])
@ip_allow(mode='all')
def set_snapshot_baseline(request, snapshot_id):
    """
    Set an existing snapshot as the baseline
    """
    try:
        from apps.monitoring.models import SiteSnapshot
        
        snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        
        # This will automatically unset any other baseline for this site
        # due to the model's save() method
        snapshot.is_baseline = True
        snapshot.save()
        
        return Response({
            'status': 'success',
            'message': f'Snapshot {snapshot_id} set as baseline'
        })
        
    except SiteSnapshot.DoesNotExist:
        return Response({
            'status': 'error',
            'message': f'Snapshot {snapshot_id} not found'
        }, status=404)