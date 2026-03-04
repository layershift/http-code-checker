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
from apps.monitoring.tasks import capture_screenshot_task
import json
import ipaddress
import inspect

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