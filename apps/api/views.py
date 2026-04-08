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
            single_ip = data.get('ip')
            
            if not names and not single_name:
                return JsonResponse({
                    'status': 'error',
                    'message': 'Either "name" (single) or "names" (list) is required'
                }, status=400)
            
            # Convert single name to list for uniform processing
            if single_name:
                names = [(single_name, single_ip)]
            
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
                for site_name, site_ip in names:
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
                            server_ip=site_ip if site_ip else None,
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
        import uuid
        message_id = str(uuid.uuid4())

        # Create initial Zulip message record
        from apps.monitoring.models import ZulipMessage
        zulip_msg = ZulipMessage.objects.create(
            message_id=message_id,
            server=server if server_name else None,
            site=site if domain_name else None,
            title=f"Monitoring: {server_name or domain_name}",
            status='pending',
            total_sites=len(results['sites']),
            sites_pending=len(results['sites']),
            ticket_id=ticket_id,
            source='api'
        )
        print(f"📝 Created Zulip tracking message: {message_id}")
        thread = threading.Thread(
            target=wait_for_completion_and_notify,
            args=(results['target'], results['sites'], results['start_time'], message_id)
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


def wait_for_completion_and_notify(target, sites_data, start_time, message_id=None):
    """
    Wait for all jobs to complete, then send monitoring results via Notify.send()
    Also updates ZulipMessage status in real-time
    """
    from rq.job import Job
    from django_rq import get_queue
    from datetime import datetime
    import time
    from apps.monitoring.util.evaluator import SiteEvaluator
    from apps.monitoring.models import SiteSnapshot, ZulipMessage
    import os
    from django.utils import timezone
    
    queue = get_queue('default')
    connection = queue.connection
    
    # Create or update Zulip message record
    zulip_msg = None
    if message_id:
        try:
            zulip_msg = ZulipMessage.objects.get(message_id=message_id)
            zulip_msg.status = 'processing'
            zulip_msg.total_sites = len(sites_data)
            zulip_msg.sites_pending = len(sites_data)
            zulip_msg.save()
            print(f"📝 Zulip message {message_id} status: PROCESSING")
        except ZulipMessage.DoesNotExist:
            # Create if doesn't exist
            target_name = target.get('name', 'Unknown')
            target_type = target.get('type', 'target')
            zulip_msg = ZulipMessage.objects.create(
                message_id=message_id,
                server_id=target.get('id') if target_type == 'server' else None,
                title=f"Monitoring: {target_name}",
                status='processing',
                total_sites=len(sites_data),
                sites_pending=len(sites_data),
                source='api'
            )
            print(f"📝 Created Zulip message {message_id} with status: PROCESSING")
    else:
        print("⚠️ No message_id provided, skipping Zulip status tracking")
    
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
    
    # Track site results for summary
    site_results = {}
    
    # Progress tracking variables
    last_progress_update = 0
    last_completed_sites = 0

    while waited < max_wait:
        completed_jobs = 0
        completed_sites = 0
        
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
    
        # Calculate completed sites
        for site_name, jobs in site_completion.items():
            if all(jobs.values()) and site_name not in site_results:
                # Site completed, evaluate its status
                site_results[site_name] = {'completed': True}
                completed_sites += 1
        
        # Update Zulip message progress (every 5 seconds or when sites complete)
        current_completed_sites = completed_sites
        if zulip_msg and (current_completed_sites != last_completed_sites or 
                          time.time() - last_progress_update > 5):
            zulip_msg.sites_processed = current_completed_sites
            zulip_msg.sites_pending = len(sites_data) - current_completed_sites
            zulip_msg.save(update_fields=['sites_processed', 'sites_pending', 'updated_at'])
            print(f"📊 Progress: {current_completed_sites}/{len(sites_data)} sites completed")
            last_completed_sites = current_completed_sites
            last_progress_update = time.time()
        
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
    
    # Build monitoring results - evaluate all sites
    warning_lines = []
    fail_lines = []
    status_changed = False
    ssim_warning = False
    
    successful_count = 0
    failed_count = 0
    warning_count = 0
    failed_sites_list = []
    warning_sites_list = []
    
    for site_info in sites_data:
        site_name = site_info['name']
        evaluator = SiteEvaluator(site_name)
        
        if evaluator.is_valid():
            passed, text = evaluator.get_monitoring_text(compact=False)
            print(f"✅ Evaluated {site_name}: {'PASS' if passed else 'FAIL'} {text}")
            
            # Check status code change
            baseline_snapshot = evaluator.baseline_snapshot
            latest_snapshot = evaluator.site.snapshots.first()
            
            baseline_status = None
            latest_status = None
            status_unchanged = True
            ssim_bad = False
            
            # Check status code change
            if baseline_snapshot and latest_snapshot:
                baseline_status = baseline_snapshot.http_status_code
                latest_status = latest_snapshot.http_status_code
                if baseline_status != latest_status:
                    status_unchanged = False
                    status_changed = True
            
            # Check SSIM if available
            if evaluator.has_comparison() and evaluator.latest_comparison:
                ssim = evaluator.latest_comparison.ssim_score
                if ssim is not None and ssim < 0.90:
                    ssim_bad = True
                    ssim_warning = True
            
            # Parse the text to get the values
            parts = text.split('|')
            if len(parts) >= 6:
                site_link = parts[1].strip()
                status_part = parts[2].strip()
                ssim_part = parts[3].strip()
                score_part = parts[4].strip()
                change_part = parts[5].strip()
            else:
                site_link = site_name
                status_part = "-"
                ssim_part = "-"
                score_part = "-"
                change_part = "-"
            
            # Categorize the site
            if not status_unchanged:
                # Status code changed -> FAIL
                formatted_line = f"| ❌ | {site_link} | {status_part} | {ssim_part} | {score_part} | {change_part} |"
                fail_lines.append(formatted_line)
                failed_count += 1
                failed_sites_list.append(site_name)
            elif ssim_bad:
                # Status unchanged but SSIM bad -> WARNING
                formatted_line = f"| ⚠️ | {site_link} | {status_part} | {ssim_part} | {score_part} | {change_part} |"
                warning_lines.append(formatted_line)
                warning_count += 1
                warning_sites_list.append(site_name)
            else:
                # All good
                successful_count += 1
            
        else:
            # Failed evaluation
            formatted_line = f"| ❌ {site_name} | Error: {evaluator.error} | - | - | - |"
            fail_lines.append(formatted_line)
            failed_count += 1
            failed_sites_list.append(site_name)
            status_changed = True
    
    # Determine final status
    if failed_count > 0:
        final_status = 'failed'
    elif warning_count > 0:
        final_status = 'partial'
    else:
        final_status = 'completed'
    
    # Update Zulip message with final results
    if zulip_msg:
        zulip_msg.status = final_status
        zulip_msg.successful_sites = successful_count
        zulip_msg.failed_sites = failed_count
        zulip_msg.warning_sites = warning_count
        zulip_msg.sites_processed = len(sites_data)
        zulip_msg.sites_pending = 0
        zulip_msg.processed_at = timezone.now()
        zulip_msg.results_summary = {
            'failed_sites': failed_sites_list,
            'warning_sites': warning_sites_list,
            'successful_count': successful_count,
            'duration_seconds': duration
        }
        zulip_msg.save()
        print(f"📝 Zulip message {message_id} status: {final_status.upper()}")
    
    # Combine all monitoring texts
    monitoring_text = ""
    if fail_lines:
        monitoring_text += "\n\n❌ **FAILURES (Status Changes)**\n"
        monitoring_text += "\n| Status | Site Name | Status | SSIM | Score | Change |\n"
        monitoring_text += "| --- | --- | --- | --- | --- | ---\n"
        monitoring_text += "\n".join(fail_lines)
    
    if warning_lines:
        monitoring_text += "\n\n⚠️ **WARNINGS (Visual Changes)**\n"
        monitoring_text += "\n| Status | Site Name | Status | SSIM | Score | Change |\n"
        monitoring_text += "| --- | --- | --- | --- | --- | ---\n"
        monitoring_text += "\n".join(warning_lines)
    
    # Prepare the full message
    if target_type == 'server':
        header = f"📊 Monitoring Report for Server: {target_name}"
    else:
        header = f"📊 Monitoring Report for Domain: {target_name}"

    # Determine overall status emoji
    if failed_count > 0:
        overall_emoji = "❌"
        overall_status_text = "FAILURES DETECTED"
    elif warning_count > 0:
        overall_emoji = "⚠️"
        overall_status_text = "WARNINGS DETECTED"
    else:
        overall_emoji = "✅"
        overall_status_text = "PASS"

    full_message = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Sites: {len(sites_data)} | Duration: {duration:.1f}s\n"
        f"Successful: {successful_count} | Failed: {failed_count} | Warnings: {warning_count}\n"
        f"Overall Status: {overall_emoji} {overall_status_text}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━\n"
    )
    
    full_message += monitoring_text
    full_message += f"\n\n━━━━━━━━━━━━━━━━━━━━━━━\n"

    # Get ticket ID
    ticket_id = "No Ticket ID"
    if sites_data:
        first_site_name = sites_data[0]['name']
        first_snapshot = SiteSnapshot.objects.filter(site__name=first_site_name).first()
        if first_snapshot and first_snapshot.ticket:
            ticket_id = first_snapshot.ticket
    
    # Send Zulip notification
    try:
        from apps.monitoring.utils import Notify
        if ticket_id is None:
            title  = f"[Server] {target_name}"
        else:
            title = f"{ticket_id} {os.getenv('ZULIP_SUBJECT', 'Monitoring')} {target_name}"
        
        Notify.send(
            title=title,
            body=full_message
        )
        print(f"✅ Monitoring results notification sent")
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")
        print(full_message)
    
    return {
        'status': final_status,
        'successful': successful_count,
        'failed': failed_count,
        'warnings': warning_count,
        'duration': duration
    }

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

@extend_schema(
    methods=['POST'],
    description="Set an existing snapshot as the new baseline for its site. This will automatically unset any previous baseline for the same site.",
    summary="Set Snapshot as Baseline",
    parameters=[
        OpenApiParameter(
            name='snapshot_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            description='ID of the snapshot to set as baseline',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="Snapshot set as baseline successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string', 'example': 'Snapshot 123 set as baseline'}
                }
            }
        ),
        404: OpenApiResponse(
            description="Snapshot not found",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string', 'example': 'Snapshot 123 not found'}
                }
            }
        ),
        500: OpenApiResponse(
            description="Internal server error",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string'}
                }
            }
        ),
    },
    tags=['snapshots'],
)
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


@extend_schema(
    methods=['DELETE'],
    description="Permanently delete a site and all its associated files from remote storage. This will also delete all snapshots, comparisons, and scores for this site.",
    summary="Delete Site by Name",
    parameters=[
        OpenApiParameter(
            name='site_name',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description='Domain name of the site to delete (e.g., example.com)',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="Site deleted successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string', 'example': 'Site "example.com" and all associated files deleted successfully'}
                }
            }
        ),
        404: OpenApiResponse(
            description="Site not found",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string', 'example': 'Site "example.com" not found'}
                }
            }
        ),
        500: OpenApiResponse(
            description="Internal server error",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string'}
                }
            }
        ),
    },
    tags=['sites'],
)
@api_view(['DELETE'])
@ip_allow(mode='all')
def delete_site_by_name(request, site_name):
    """
    Delete a site by name and all its associated remote files
    DELETE /api/v1/sites/example.com/delete/
    """
    try:
        from apps.monitoring.models import Site
        
        site = Site.objects.get(name=site_name.lower().strip())
        site_name_deleted = site.name
        
        # The pre_delete signal will handle file cleanup
        site.delete()
        
        return Response({
            'status': 'success',
            'message': f'Site "{site_name_deleted}" and all associated files deleted successfully'
        })
        
    except Site.DoesNotExist:
        return Response({
            'status': 'error',
            'message': f'Site "{site_name}" not found'
        }, status=404)
    except Exception as e:
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)


@extend_schema(
    methods=['DELETE'],
    description="Permanently delete a server and all its associated sites, snapshots, comparisons, and files from remote storage.",
    summary="Delete Server by Name",
    parameters=[
        OpenApiParameter(
            name='server_name',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.PATH,
            description='Name of the server to delete (URL-encode spaces)',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="Server deleted successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string', 'example': 'Server "Web Server 1" and 5 site(s) with all associated files deleted successfully'}
                }
            }
        ),
        404: OpenApiResponse(
            description="Server not found",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string', 'example': 'Server "Web Server 1" not found'}
                }
            }
        ),
        500: OpenApiResponse(
            description="Internal server error",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string'}
                }
            }
        ),
    },
    tags=['servers'],
)
@api_view(['DELETE'])
@ip_allow(mode='all')
def delete_server_by_name(request, server_name):
    """
    Delete a server by name and all its associated remote files
    DELETE /api/v1/servers/Web%20Server%201/delete/
    """
    try:
        from apps.monitoring.models import Server
        
        server = Server.objects.get(name=server_name)
        server_name_deleted = server.name
        
        # Count sites before deletion
        sites_count = server.domains.count()
        
        # The pre_delete signal will handle file cleanup for all sites
        server.delete()
        
        return Response({
            'status': 'success',
            'message': f'Server "{server_name_deleted}" and {sites_count} site(s) with all associated files deleted successfully'
        })
        
    except Server.DoesNotExist:
        return Response({
            'status': 'error',
            'message': f'Server "{server_name}" not found'
        }, status=404)
    except Exception as e:
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)


@extend_schema(
    methods=['DELETE'],
    description="Permanently delete a snapshot and its associated screenshot file from remote storage.",
    summary="Delete Snapshot by ID",
    parameters=[
        OpenApiParameter(
            name='snapshot_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            description='ID of the snapshot to delete',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(
            description="Snapshot deleted successfully",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'message': {'type': 'string', 'example': 'Snapshot 12345 and its associated file deleted successfully'}
                }
            }
        ),
        404: OpenApiResponse(
            description="Snapshot not found",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string', 'example': 'Snapshot 12345 not found'}
                }
            }
        ),
        500: OpenApiResponse(
            description="Internal server error",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'error'},
                    'message': {'type': 'string'}
                }
            }
        ),
    },
    tags=['snapshots'],
)
@api_view(['DELETE'])
@ip_allow(mode='all')
def delete_snapshot_by_id(request, snapshot_id):
    """
    Delete a snapshot by ID and its remote file
    DELETE /api/v1/snapshots/123/delete/
    """
    try:
        from apps.monitoring.models import SiteSnapshot
        
        snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        snapshot_id_deleted = snapshot.id
        
        # The model's delete() method handles file cleanup
        snapshot.delete()
        
        return Response({
            'status': 'success',
            'message': f'Snapshot {snapshot_id_deleted} and its associated file deleted successfully'
        })
        
    except SiteSnapshot.DoesNotExist:
        return Response({
            'status': 'error',
            'message': f'Snapshot {snapshot_id} not found'
        }, status=404)
    except Exception as e:
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=500)
    


@extend_schema(
    methods=['POST'],
    description="Check if all sites on a server have valid, processed baselines (within configured age limit and with valid status codes).",
    summary="Check Server Baseline Health",
    request={
        'application/json': {
            'type': 'object',
            'properties': {
                'server': {'type': 'string', 'description': 'Server name to check', 'example': 'Web Server 1'},
            },
            'required': ['server'],
        }
    },
    responses={
        200: OpenApiResponse(
            description="Health check result",
            response={
                'type': 'object',
                'properties': {
                    'status': {'type': 'string', 'example': 'success'},
                    'result': {'type': 'boolean', 'example': True},
                    'message': {'type': 'string'},
                    'details': {
                        'type': 'object',
                        'properties': {
                            'total_sites': {'type': 'integer'},
                            'sites_with_valid_baseline': {'type': 'integer'},
                            'sites_without_baseline': {'type': 'array'},
                            'sites_with_pending_baseline': {'type': 'array'},
                            'sites_with_expired_baseline': {'type': 'array'},
                            'sites_with_error_status': {'type': 'array'},
                            'max_baseline_age_days': {'type': 'integer'}
                        }
                    }
                }
            }
        ),
        400: OpenApiResponse(description="Bad request - missing server name"),
        404: OpenApiResponse(description="Server not found"),
    },
    tags=['monitoring'],
)
@api_view(['POST'])
@ip_allow(mode='all')
def check_server_baseline_health(request):
    """
    Check if all sites on a server have valid, processed baselines
    Returns True if all sites have baselines with:
    - Screenshot captured (http_status_code != 0)
    - Not older than MAX_BASELINE_AGE_DAYS
    - Status code is not an error (optional, configurable)
    """
    from django.utils import timezone
    from datetime import timedelta
    from django.conf import settings
    
    try:
        data = request.data
        
        server_name = data.get('server')
        
        if not server_name:
            return Response({
                'status': 'error',
                'message': 'Server name is required in payload'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get the server
        try:
            server = Server.objects.get(name=server_name)
        except Server.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Server "{server_name}" not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Get max baseline age from settings (default 7 days)
        max_age_days = getattr(settings, 'MAX_BASELINE_AGE_DAYS', 7)
        cutoff_date = timezone.now() - timedelta(days=max_age_days)
        
        # Whether to consider error status codes as invalid (default: True)
        consider_errors_as_invalid = getattr(settings, 'BASELINE_CONSIDER_ERRORS_INVALID', True)
        
        # Get all active sites on this server
        sites = server.domains.filter(is_active=True)
        total_sites = sites.count()
        
        if total_sites == 0:
            return Response({
                'status': 'success',
                'result': True,
                'message': 'No active sites on this server',
                'details': {
                    'total_sites': 0,
                    'sites_with_valid_baseline': 0,
                    'sites_without_baseline': [],
                    'sites_with_pending_baseline': [],
                    'sites_with_expired_baseline': [],
                    'sites_with_error_status': [],
                    'max_baseline_age_days': max_age_days
                }
            })
        
        sites_without_baseline = []
        sites_with_pending_baseline = []
        sites_with_expired_baseline = []
        sites_with_error_status = []
        
        for site in sites:
            # Get the baseline snapshot for this site
            baseline = site.snapshots.filter(is_baseline=True).first()
            
            if not baseline:
                sites_without_baseline.append(site.name)
                continue
            
            # Check if baseline has been processed (http_status_code != 0)
            if baseline.http_status_code == 0 or baseline.http_status_code is None:
                sites_with_pending_baseline.append({
                    'name': site.name,
                    'baseline_id': baseline.id,
                    'taken_at': baseline.taken_at.isoformat()
                })
                continue
            
            # Check if baseline is too old
            if baseline.taken_at < cutoff_date:
                sites_with_expired_baseline.append({
                    'name': site.name,
                    'baseline_id': baseline.id,
                    'baseline_date': baseline.taken_at.isoformat(),
                    'age_days': (timezone.now() - baseline.taken_at).days,
                    'http_status_code': baseline.http_status_code
                })
                continue
            
            # Check if baseline has error status code (optional)
            if consider_errors_as_invalid and baseline.http_status_code >= 400:
                sites_with_error_status.append({
                    'name': site.name,
                    'baseline_id': baseline.id,
                    'http_status_code': baseline.http_status_code,
                    'taken_at': baseline.taken_at.isoformat()
                })
                continue
        
        # Count valid baselines
        valid_count = total_sites - (len(sites_without_baseline) + len(sites_with_pending_baseline) + 
                                      len(sites_with_expired_baseline) + len(sites_with_error_status))
        
        # Check if all sites are healthy
        all_healthy = (valid_count == total_sites)
        
        # Build message
        if all_healthy:
            message = f"All {total_sites} site(s) have valid, processed baselines (within {max_age_days} days)"
        else:
            issues = []
            if sites_without_baseline:
                issues.append(f"{len(sites_without_baseline)} site(s) without baseline")
            if sites_with_pending_baseline:
                issues.append(f"{len(sites_with_pending_baseline)} site(s) with pending baseline (not yet processed)")
            if sites_with_expired_baseline:
                issues.append(f"{len(sites_with_expired_baseline)} site(s) with expired baseline (> {max_age_days} days)")
            if sites_with_error_status:
                issues.append(f"{len(sites_with_error_status)} site(s) with error status codes")
            message = f"Baseline issues found: {', '.join(issues)}"
        
        return Response({
            'status': 'success',
            'result': all_healthy,
            'message': message,
            'details': {
                'total_sites': total_sites,
                'sites_with_valid_baseline': valid_count,
                'sites_without_baseline': sites_without_baseline,
                'sites_with_pending_baseline': sites_with_pending_baseline,
                'sites_with_expired_baseline': sites_with_expired_baseline,
                'sites_with_error_status': sites_with_error_status,
                'max_baseline_age_days': max_age_days,
                'consider_errors_as_invalid': consider_errors_as_invalid
            }
        })
        
    except Exception as e:
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@extend_schema(
    methods=['GET'],
    description="Get the status of a monitoring job by message ID",
    summary="Get Monitoring Job Status",
    parameters=[
        OpenApiParameter(
            name='message_id',
            type=OpenApiTypes.STR,
            location=OpenApiParameter.QUERY,
            description='The message ID returned from the dispatch_comparison call',
            required=True,
        ),
    ],
    responses={
        200: OpenApiResponse(description="Status retrieved"),
        404: OpenApiResponse(description="Message not found"),
    },
    tags=['monitoring'],
)
@api_view(['GET'])
@ip_allow(mode='all')
def get_monitoring_status(request):
    """
    Get the status of a monitoring job by message ID
    """
    from apps.monitoring.models import ZulipMessage
    
    try:
        message_id = request.query_params.get('ticket_id') or request.query_params.get('message_id')
        
        if not message_id:
            return Response({
                'status': 'error',
                'message': 'message_id parameter is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            msg = ZulipMessage.objects.filter(ticket_id=message_id).first()
        except ZulipMessage.DoesNotExist:
            return Response({
                'status': 'error',
                'message': f'Message with ID "{message_id}" not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Determine if job is complete
        is_complete = msg.status in ['completed', 'failed', 'partial']
        
        # Calculate duration manually if processed_at exists
        duration = None
        if msg.processed_at:
            duration = (msg.processed_at - msg.created_at).total_seconds()
        
        return Response({
            'status': 'success',
            'message_id': msg.message_id,
            'job_status': msg.status,
            'is_complete': is_complete,
            'progress': {
                'total_sites': msg.total_sites,
                'processed_sites': msg.sites_processed,
                'pending_sites': msg.sites_pending,
                'percentage': int((msg.sites_processed / msg.total_sites * 100)) if msg.total_sites > 0 else 0
            },
            'results': {
                'successful_sites': msg.successful_sites,
                'failed_sites': msg.failed_sites,
                'warning_sites': msg.warning_sites,
                'is_healthy': msg.failed_sites == 0 and msg.warning_sites == 0 and msg.status == 'completed'
            } if is_complete else None,
            'timing': {
                'created_at': msg.created_at.isoformat(),
                'updated_at': msg.updated_at.isoformat(),
                'processed_at': msg.processed_at.isoformat() if msg.processed_at else None,
                'duration_seconds': duration
            }
        })
        
    except Exception as e:
        return Response({
            'status': 'error',
            'message': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)