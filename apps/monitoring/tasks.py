# monitoring/tasks.py
import os
import sys
import tempfile
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import close_old_connections
from django.conf import settings
from rq import get_current_job
from playwright.sync_api import sync_playwright
from datetime import timedelta
from django_rq import job, get_queue
from rq.registry import StartedJobRegistry, ScheduledJobRegistry, FinishedJobRegistry
import requests
import json

print("🔄 Loading tasks module...")

# Check if remote uploader is enabled
REMOTE_UPLOADER_ENABLED = getattr(settings, 'REMOTE_UPLOADER_ENABLED', False)
REMOTE_UPLOADER_URL = getattr(settings, 'REMOTE_UPLOADER_URL', 'http://dont-delete-uploader.man-1.solus.stage.town:8000')

# Import storage class directly if remote uploader is enabled
remote_storage = None
if REMOTE_UPLOADER_ENABLED:
    try:
        from .storage import RemoteUploaderStorage
        remote_storage = RemoteUploaderStorage()
        print(f"✅ RemoteUploaderStorage initialized: {remote_storage}")
    except Exception as e:
        print(f"❌ Failed to initialize RemoteUploaderStorage: {e}")
        REMOTE_UPLOADER_ENABLED = False


def save_to_storage(instance, field_name, filename, file_data):
    """
    Save file using appropriate storage - saves the ENTIRE instance
    """
    try:
        field = getattr(instance, field_name)
        
        if remote_storage:
            # For remote storage: upload and set the field value to file_id
            file_id = remote_storage._save(filename, ContentFile(file_data))
            setattr(instance, field_name, file_id)
            # Save the ENTIRE instance (saves all fields)
            instance.save()
            print(f"✅ File uploaded to remote: {file_id}")
            print(f"   URL: {remote_storage.url(file_id)}")
        else:
            # For local storage: use Django's save
            field.save(filename, ContentFile(file_data), save=True)
            print(f"✅ File saved locally: {field.path}")
        
        return True
    except Exception as e:
        print(f"❌ Save failed: {e}")
        import traceback
        traceback.print_exc()
        return False


@job('default', result_ttl=3600)
def capture_screenshot_task(snapshot_id, site_name, site_id):
    """
    Task 1: Capture screenshot for a snapshot - then trigger comparison
    """
    current_job = get_current_job()
    print(f"🎯 [Job {current_job.id}] Starting screenshot capture for snapshot {snapshot_id}, site: {site_name}")
    
    close_old_connections()
    temp_path = None
    screenshot_saved = False
    
    browser_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:115.0) Gecko/20100101 Firefox/115.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
    }

    try:
        from .models import SiteSnapshot, ScreenshotComparison
        from .comparison import compare_screenshots
        
        # Get snapshot
        snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        print(f"📸 Processing screenshot for {site_name}")
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            temp_path = tmp_file.name
            print(f"📁 Temp file created: {temp_path}")
        
        # Prepare URL
        url = site_name
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
        
        print(f"🌐 Accessing URL: {url}")
        
        status_code = 500
        content_length = 0
        screenshot_data = None
        
        # Take screenshot - sync Playwright
        try:
            with sync_playwright() as p:
                print("🚀 Launching browser...")
                browser = p.chromium.launch(
                    headless=True, 
                    args=[
                        "--no-sandbox", 
                        "--disable-gpu",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=VizDisplayCompositor",
                        "--disable-dev-shm-usage",
                        "--js-flags=--max-old-space-size=512",
                        "--disable-setuid-sandbox",
                        "--no-first-run",
                        "--no-zygote",
                        "--disable-logging"
                    ]
                )
                context = browser.new_context(
                    viewport={'width': 800, 'height': 600},
                    device_scale_factor=1
                )
                print("✅ Browser launched")
                
                page = context.new_page()
                print("✅ Page created")
                
                def intercept_request(route, request):
                    headers = request.headers
                    modified_headers = {**headers, **browser_headers}
                    route.continue_(headers=modified_headers)
                
                page.route("**/*", intercept_request)
                
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {
                        get: () => undefined
                    });
                    Object.defineProperty(navigator, 'plugins', {
                        get: () => [1, 2, 3, 4, 5]
                    });
                    Object.defineProperty(navigator, 'languages', {
                        get: () => ['en-US', 'en']
                    });
                """)
                
                try:
                    print(f"⏳ Navigating to {url}...")
                    response = page.goto(url, wait_until="networkidle", timeout=30000)
                    status_code = response.status if response else 500
                    print(f"✅ Got status code: {status_code}")
                    
                    print("📸 Taking screenshot...")
                    page.screenshot(
                        path=temp_path, 
                        full_page=True,
                        type='png',
                        omit_background=False, 
                        animations='disabled'
                    )
                    print(f"✅ Screenshot saved to {temp_path}")
                    
                    print("📊 Getting page content...")
                    content = page.content()
                    content_length = len(content.encode('utf-8'))
                    print(f"📊 Content length: {content_length}")
                    
                    with open(temp_path, 'rb') as f:
                        screenshot_data = f.read()
                    print(f"💾 Screenshot size: {len(screenshot_data)} bytes")
                    
                except Exception as e:
                    print(f"❌ Browser navigation error: {e}")
                    status_code = 500
                    content_length = 0
                    
                finally:
                    print("🔄 Closing browser...")
                    browser.close()
                    print("✅ Browser closed")
                    
        except Exception as e:
            print(f"❌ Playwright error: {e}")
            status_code = 500
            content_length = 0
        
        # Update snapshot with status code and content length
        print("💾 Updating snapshot in database...")
        
        # Refresh snapshot
        try:
            snapshot.refresh_from_db()
        except:
            close_old_connections()
            snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        
        snapshot.http_status_code = status_code
        snapshot.content_length = content_length
        
        # Save based on whether we have screenshot data
        if screenshot_data and status_code and status_code < 400:
            filename = f"site_{site_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.png"
            print(f"💾 Saving screenshot as: {filename}")
            
            # Save the screenshot and the entire snapshot (status_code, content_length, etc.)
            success = save_to_storage(snapshot, 'screenshot', filename, screenshot_data)
            
            if success:
                screenshot_saved = True
                print(f"✅ Screenshot saved for snapshot {snapshot_id}")
                print(f"   HTTP Status: {snapshot.http_status_code}")
                print(f"   Content Length: {snapshot.content_length}")
                
                # Trigger comparison and score jobs
                print("🔍 Triggering comparison job...")
                from .tasks import create_comparison_task
                comparison_job = create_comparison_task.delay(snapshot_id, site_id)
                print(f"🚀 Enqueued comparison job: {comparison_job.id}")
                
                score_job = calculate_site_score_task.delay(snapshot_id)
                print(f"📊 Enqueued site score job: {score_job.id}")
                
            else:
                # Screenshot save failed, but we still have status_code and content_length
                snapshot.save()
                print(f"⚠️ Snapshot {snapshot_id} saved without screenshot (status: {status_code})")
        else:
            # No screenshot, just save status_code and content_length
            snapshot.save()
            print(f"⚠️ Snapshot {snapshot_id} saved without screenshot (status: {status_code})")
        
        # Return result
        return {
            'snapshot_id': snapshot_id,
            'site_id': site_id,
            'screenshot_saved': screenshot_saved,
            'status_code': status_code,
            'content_length': content_length,
            'comparison_triggered': screenshot_saved
        }
        
    except Exception as e:
        print(f"❌ Critical error in capture task: {e}")
        import traceback
        traceback.print_exc()
        return {
            'snapshot_id': snapshot_id,
            'site_id': site_id,
            'screenshot_saved': False,
            'error': str(e)
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
                print(f"🧹 Cleaned up temp file: {temp_path}")
            except Exception as e:
                print(f"⚠️ Failed to clean up temp file: {e}")
        close_old_connections()
        print(f"🏁 Capture task finished for snapshot {snapshot_id}")


# Keep the rest of your tasks (create_comparison_task, calculate_site_score_task, 
# monitor_site_score_task, has_other_pending_monitoring, list_all_jobs) exactly as they are...

@job('comparison', result_ttl=3600)
def create_comparison_task(snapshot_id, site_id):
    """
    Task 2: Create comparison with baseline snapshot and upload results to remote storage
    """
    current_job = get_current_job()
    print(f"🔍 [Job {current_job.id}] Starting comparison for snapshot {snapshot_id}")
    
    close_old_connections()
    
    try:
        from .models import SiteSnapshot, ScreenshotComparison
        from .comparison import compare_screenshots
        
        # Get current snapshot
        current_snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        
        # Check if screenshot exists
        if not current_snapshot.screenshot:
            print(f"⚠️ Snapshot {snapshot_id} has no screenshot, skipping comparison")
            return {
                'snapshot_id': snapshot_id,
                'comparison_created': False,
                'reason': 'no_screenshot'
            }
        
        print(f"✅ Current snapshot has screenshot: {current_snapshot.screenshot.url}")
        
        # Find baseline snapshot for this site
        baseline_snapshot = SiteSnapshot.objects.filter(
            site_id=site_id,
            is_baseline=True,
            screenshot__isnull=False
        ).first()
        
        if not baseline_snapshot:
            print(f"📭 No baseline snapshot found for this site")
            if SiteSnapshot.objects.filter(site_id=site_id, screenshot__isnull=False).count() == 1:
                current_snapshot.is_baseline = True
                current_snapshot.save()
                print(f"✅ Set snapshot {snapshot_id} as baseline (first screenshot)")
            return {
                'snapshot_id': snapshot_id,
                'comparison_created': False,
                'reason': 'no_baseline'
            }
        
        # Don't compare with itself
        if baseline_snapshot.id == current_snapshot.id:
            print(f"ℹ️ Current snapshot is the baseline, no comparison needed")
            return {
                'snapshot_id': snapshot_id,
                'comparison_created': False,
                'reason': 'is_baseline'
            }
        
        print(f"✅ Found baseline snapshot ID: {baseline_snapshot.id} from {baseline_snapshot.taken_at}")
        
        # Check if comparison already exists
        existing_comparison = ScreenshotComparison.objects.filter(
            previous_snapshot=baseline_snapshot,
            current_snapshot=current_snapshot
        ).first()
        
        if existing_comparison:
            print(f"⚠️ Comparison already exists: ID {existing_comparison.id}")
            return {
                'snapshot_id': snapshot_id,
                'comparison_created': False,
                'comparison_id': existing_comparison.id,
                'reason': 'already_exists'
            }
        
        # Create temp directory for comparison images
        with tempfile.TemporaryDirectory() as temp_dir:
            print(f"📁 Created temp dir for comparison: {temp_dir}")
            
            # Compare screenshots - this will generate heatmap and diff images in temp_dir
            result = compare_screenshots(baseline_snapshot, current_snapshot, output_dir=temp_dir)
            
            if result.get('error'):
                print(f"❌ Comparison failed: {result['error']}")
                return {
                    'snapshot_id': snapshot_id,
                    'comparison_created': False,
                    'error': result['error']
                }
            
            if result.get('ssim_score') is None:
                print(f"❌ Comparison returned no SSIM score")
                return {
                    'snapshot_id': snapshot_id,
                    'comparison_created': False,
                    'error': 'No SSIM score returned'
                }
            
            print(f"📊 SSIM Score vs Baseline: {result['ssim_score']:.4f}")
            print(f"📊 Change from Baseline: {result['percent_difference']:.2f}%")
            
            # Create comparison object
            comparison = ScreenshotComparison.objects.create(
                site_id=site_id,
                previous_snapshot=baseline_snapshot,
                current_snapshot=current_snapshot,
                ssim_score=result['ssim_score'],
                percent_difference=result['percent_difference'],
                changed_pixels=result['changed_pixels'],
                total_pixels=result['total_pixels']
            )
            print(f"✅ Created comparison ID: {comparison.id}")
            
            # Upload heatmap to remote storage if generated
            if result.get('heatmap_image_path') and os.path.exists(result['heatmap_image_path']):
                print(f"📤 Uploading heatmap to remote storage...")
                with open(result['heatmap_image_path'], 'rb') as f:
                    heatmap_data = f.read()
                filename = f"heatmap_{baseline_snapshot.id}_vs_{current_snapshot.id}.png"
                save_to_storage(comparison, 'heatmap', filename, heatmap_data)
                print(f"✅ Heatmap uploaded: {comparison.heatmap.url if comparison.heatmap else 'Failed'}")
            else:
                print(f"⚠️ No heatmap generated")
            
            # Upload diff image to remote storage if generated
            if result.get('diff_image_path') and os.path.exists(result['diff_image_path']):
                print(f"📤 Uploading diff image to remote storage...")
                with open(result['diff_image_path'], 'rb') as f:
                    diff_data = f.read()
                filename = f"diff_{baseline_snapshot.id}_vs_{current_snapshot.id}.png"
                save_to_storage(comparison, 'diff_image', filename, diff_data)
                print(f"✅ Diff image uploaded: {comparison.diff_image.url if comparison.diff_image else 'Failed'}")
            else:
                print(f"⚠️ No diff image generated")
            
            print(f"✅ Comparison with baseline completed for snapshot {snapshot_id}")
            
            return {
                'snapshot_id': snapshot_id,
                'comparison_created': True,
                'comparison_id': comparison.id,
                'ssim_score': result['ssim_score'],
                'percent_difference': result['percent_difference'],
                'baseline_id': baseline_snapshot.id,
                'heatmap_uploaded': bool(comparison.heatmap),
                'diff_uploaded': bool(comparison.diff_image)
            }
            
    except Exception as e:
        print(f"❌ Critical error in comparison task: {e}")
        import traceback
        traceback.print_exc()
        return {
            'snapshot_id': snapshot_id,
            'comparison_created': False,
            'error': str(e)
        }
    finally:
        close_old_connections()


@job('scoring', result_ttl=3600)
def calculate_site_score_task(snapshot_id):
    """
    Calculate quality scores for a site based on its snapshot
    """
    from .models import SiteSnapshot, SiteScore
    from .services.scoring import SiteScoringService
    
    try:
        snapshot = SiteSnapshot.objects.select_related('site').get(id=snapshot_id)
        print(f"📊 Calculating scores for {snapshot.site.name}")
        
        # Run evaluation
        service = SiteScoringService(snapshot.site.name)
        scores = service.evaluate()
        
        # Create score record
        site_score = SiteScore.objects.create(
            site=snapshot.site,
            snapshot=snapshot,
            performance_score=scores.get('performance'),
            seo_score=scores.get('seo'),
            security_score=scores.get('security'),
            availability_score=scores.get('availability'),
            overall_score=scores.get('overall'),
            page_load_time_ms=scores['metrics'].get('ttfb_ms'),
            content_size_kb=scores['metrics'].get('content_size_kb'),
            has_ssl=scores['metrics'].get('has_ssl', False),
            has_security_headers=scores['metrics'].get('has_hsts', False),
        )
        
        print(f"✅ Site score {site_score.overall_score} recorded for {snapshot.site.name}")
        
        return {
            'snapshot_id': snapshot_id,
            'score_id': site_score.id,
            'overall_score': site_score.overall_score
        }
        
    except Exception as e:
        print(f"❌ Error calculating site score: {e}")
        import traceback
        traceback.print_exc()
        return {'snapshot_id': snapshot_id, 'error': str(e)}


@job('monitoring')
def monitor_site_score_task(site_id):
    """
    Monitoring task - calculates site score and schedules next run
    """
    current_job = get_current_job()
    current_job_id = current_job.id if current_job else None
    
    print(f"\n{'='*60}")
    print(f"🔍 [Job {current_job_id}] Starting score monitoring for site {site_id}")
    print(f"{'='*60}")
    sys.stdout.flush()
    
    close_old_connections()
    
    try:
        from .models import Site, SiteSnapshot, SiteScore
        from .services.scoring import SiteScoringService
        
        # Get site
        site = Site.objects.get(id=site_id)
        print(f"📊 Site: {site.name}")
        print(f"📊 Continuous monitoring: {site.continuous_monitoring}")
        print(f"📊 Frequency: {site.monitoring_frequency} minutes")
        sys.stdout.flush()
        
        # Check if site should be monitored
        if not site.continuous_monitoring or not site.is_active:
            print(f"⏹️ Site {site.name} has monitoring disabled, skipping")
            sys.stdout.flush()
            return {
                'site_id': site_id,
                'status': 'skipped',
                'reason': 'monitoring_disabled'
            }
        
        # Create a lightweight "score-only" snapshot
        snapshot = SiteSnapshot.objects.create(
            site=site,
            http_status_code=0,
            content_length=0
        )
        print(f"✅ Created snapshot ID: {snapshot.id}")
        sys.stdout.flush()
        
        # Calculate site score
        scoring_service = SiteScoringService(site.name)
        scores = scoring_service.evaluate()
        
        # Create score record
        site_score = SiteScore.objects.create(
            site=site,
            snapshot=snapshot,
            performance_score=scores.get('performance'),
            seo_score=scores.get('seo'),
            security_score=scores.get('security'),
            availability_score=scores.get('availability'),
            overall_score=scores.get('overall'),
            page_load_time_ms=scores['metrics'].get('ttfb_ms'),
            content_size_kb=scores['metrics'].get('content_size_kb'),
            has_ssl=scores['metrics'].get('has_ssl', False),
        )
        
        print(f"✅ Score calculated: {site_score.overall_score:.1f}")
        sys.stdout.flush()
        
        # Update snapshot with status code
        snapshot.http_status_code = scores['metrics'].get('status_code', 0)
        snapshot.save(update_fields=['http_status_code'])
        
        # Update last monitored time
        site.last_monitored = timezone.now()
        site.save(update_fields=['last_monitored'])
        
        # SCHEDULE NEXT RUN - EXCLUDE CURRENT JOB
        print(f"\n⏰ Attempting to schedule next run...")
        sys.stdout.flush()
        
        # Calculate next run time
        next_run = timezone.now() + timedelta(minutes=site.monitoring_frequency)
        print(f"📅 Next run calculated for: {next_run}")
        sys.stdout.flush()
        
        # Check if there's already another scheduled job for this site (excluding current)
        queue = get_queue('monitoring')
        
        if not has_other_pending_monitoring(site_id, current_job_id):
            # Schedule the next job
            next_job = queue.enqueue_at(
                next_run,
                monitor_site_score_task,
                site_id
            )
            print(f"✅ Scheduled next job: {next_job.id} for {next_run}")
            sys.stdout.flush()
        else:
            print(f"⏭️ Another job already pending for site {site_id}, skipping scheduling")
            sys.stdout.flush()
        
        print(f"\n✅ Job completed successfully")
        print(f"{'='*60}")
        sys.stdout.flush()
        
        return {
            'site_id': site_id,
            'snapshot_id': snapshot.id,
            'score_id': site_score.id,
            'overall_score': site_score.overall_score,
            'next_run': next_run.isoformat()
        }
        
    except Site.DoesNotExist:
        print(f"❌ Site {site_id} not found")
        sys.stdout.flush()
        return {'site_id': site_id, 'error': 'Site not found'}
    except Exception as e:
        print(f"❌ Error monitoring site: {e}")
        import traceback
        traceback.print_exc()
        sys.stdout.flush()
        return {'site_id': site_id, 'error': str(e)}
    finally:
        close_old_connections()


def has_other_pending_monitoring(site_id, current_job_id=None):
    """Check if there's another pending or running job for this site (excluding current)"""
    from django_rq import get_queue
    from rq.job import Job
    from rq.registry import StartedJobRegistry, ScheduledJobRegistry
    
    queue = get_queue('monitoring')
    connection = queue.connection
    
    # Check scheduled jobs (future)
    scheduled = ScheduledJobRegistry('monitoring', connection)
    for job_id in scheduled.get_job_ids():
        if job_id == current_job_id:
            continue
        try:
            job = Job.fetch(job_id, connection=connection)
            args = job.args
            if args and len(args) > 0 and args[0] == site_id:
                print(f"⚠️ Found other SCHEDULED job for site {site_id}: {job_id}")
                return True
        except:
            continue
    
    # Check started jobs (currently running) - exclude current
    started = StartedJobRegistry('monitoring', connection)
    for job_id in started.get_job_ids():
        if job_id == current_job_id:
            continue
        try:
            job = Job.fetch(job_id, connection=connection)
            args = job.args
            if args and len(args) > 0 and args[0] == site_id:
                print(f"⚠️ Found other STARTED job for site {site_id}: {job_id}")
                return True
        except:
            continue
    
    return False


def list_all_jobs(request):
    """List all jobs in the monitoring queue"""
    from django.http import JsonResponse
    from django_rq import get_queue
    from rq.job import Job
    from rq.registry import StartedJobRegistry, ScheduledJobRegistry, FinishedJobRegistry, FailedJobRegistry
    
    queue = get_queue('monitoring')
    connection = queue.connection
    
    result = {}
    
    registries = [
        ('scheduled', ScheduledJobRegistry('monitoring', connection)),
        ('started', StartedJobRegistry('monitoring', connection)),
        ('finished', FinishedJobRegistry('monitoring', connection)),
        ('failed', FailedJobRegistry('monitoring', connection)),
    ]
    
    for name, registry in registries:
        jobs = []
        for job_id in registry.get_job_ids():
            try:
                job = Job.fetch(job_id, connection=connection)
                jobs.append({
                    'id': job_id,
                    'site_id': job.args[0] if job.args else None,
                    'enqueued_at': job.enqueued_at.isoformat() if job.enqueued_at else None,
                })
            except:
                jobs.append({'id': job_id, 'error': 'Could not fetch'})
        result[name] = jobs
    
    return JsonResponse(result)


# ===========================================================================
# SERIAL SERVER-REPORT GATE
# Runs on the single-worker 'server_reports' queue.
# One job = one full report lifecycle (wait -> dispatch -> wait -> notify).
# ===========================================================================

import time
from datetime import datetime
from django.db import connection as _db_connection
from rq import Worker
from rq.job import Job
from rq.registry import StartedJobRegistry, DeferredJobRegistry


DEFAULT_QUEUE_NAME = 'default'


def _default_queue_is_empty() -> bool:
    """True iff nothing is queued, running, or deferred on the 'default' queue."""
    q = get_queue(DEFAULT_QUEUE_NAME)
    conn = q.connection

    if q.count > 0:
        return False

    for worker in Worker.all(connection=conn):
        if DEFAULT_QUEUE_NAME not in [w.name for w in worker.queues]:
            continue
        if worker.get_state() == 'busy':
            return False
        if worker.get_current_job_id():
            return False

    if StartedJobRegistry(queue=q).count > 0:
        return False
    if DeferredJobRegistry(queue=q).count > 0:
        return False

    return True


def run_server_report(server_name=None, domain_name=None,
                     ticket_id=None, message_id=None):
    """
    ONE job = ONE full report lifecycle.
    Occupies the single 'server_reports' worker from start to Zulip post.
    """
    from .models import Site, Server, SiteSnapshot, ZulipMessage
    from .util.evaluator import SiteEvaluator
    from .utils import Notify

    close_old_connections()
    start_time = datetime.now()

    zulip_msg = (
        ZulipMessage.objects.filter(message_id=message_id).first()
        if message_id else None
    )

    def set_status(status, **fields):
        if not zulip_msg:
            return
        zulip_msg.status = status
        for k, v in fields.items():
            setattr(zulip_msg, k, v)
        zulip_msg.save()

    try:
        # ---------- Phase 1: wait for default queue to drain ----------
        set_status('waiting_for_queue')
        waited = 0
        GATE_TIMEOUT = 3600
        while not _default_queue_is_empty():
            if waited >= GATE_TIMEOUT:
                set_status('failed', results_summary={'error': 'gate timeout'})
                return {'status': 'failed', 'reason': 'gate_timeout'}
            time.sleep(3)
            waited += 3
            close_old_connections()

        # ---------- Phase 2: resolve target + dispatch ----------
        set_status('processing')

        if domain_name:
            try:
                sites = [Site.objects.get(name=domain_name.lower().strip())]
            except Site.DoesNotExist:
                set_status('failed', results_summary={'error': 'domain missing'})
                return {'status': 'failed', 'reason': 'domain_missing'}
            target = {'type': 'domain', 'name': sites[0].name, 'id': sites[0].id}
        else:
            try:
                srv = Server.objects.get(name=server_name)
            except Server.DoesNotExist:
                set_status('failed', results_summary={'error': 'server missing'})
                return {'status': 'failed', 'reason': 'server_missing'}
            sites = list(srv.domains.filter(is_active=True))
            target = {'type': 'server', 'name': srv.name, 'id': srv.id,
                      'site_count': len(sites)}

        if not sites:
            set_status('completed',
                       successful_sites=0, failed_sites=0, warning_sites=0,
                       sites_processed=0, sites_pending=0,
                       processed_at=timezone.now(),
                       results_summary={'note': 'no active sites'})
            return {'status': 'completed', 'note': 'no_active_sites'}

        work_q = get_queue(DEFAULT_QUEUE_NAME)
        sites_data = []
        for site in sites:
            snap = SiteSnapshot.objects.create(
                site=site, http_status_code=0, content_length=0, ticket=ticket_id,
            )
            job = work_q.enqueue(
                capture_screenshot_task,
                snap.id, site.name, site.id,
                job_timeout=1800,
            )
            sites_data.append({'name': site.name, 'jobs': {'screenshot': job.id}})

        set_status('processing',
                   total_sites=len(sites_data),
                   sites_pending=len(sites_data),
                   sites_processed=0)

        # ---------- Phase 3: wait for snapshot jobs ----------
        all_ids = [j for s in sites_data for j in s['jobs'].values()]
        waited = 0
        JOB_TIMEOUT = 3600
        while waited < JOB_TIMEOUT:
            done = 0
            for jid in all_ids:
                try:
                    job = Job.fetch(jid, connection=work_q.connection)
                    if job.get_status() in ('finished', 'failed'):
                        done += 1
                except Exception:
                    done += 1
            if done >= len(all_ids):
                break
            if zulip_msg:
                zulip_msg.sites_processed = done
                zulip_msg.sites_pending = len(all_ids) - done
                zulip_msg.save(update_fields=[
                    'sites_processed', 'sites_pending', 'updated_at'
                ])
            time.sleep(5)
            waited += 5
            close_old_connections()

        duration = (datetime.now() - start_time).total_seconds()

        # ---------- Phase 4: evaluate + notify ----------
        return _run_evaluation_and_notify(
            zulip_msg=zulip_msg,
            target=target,
            sites_data=sites_data,
            duration=duration,
            ticket_id=ticket_id,
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        if zulip_msg:
            from .models import ZulipMessage
            ZulipMessage.objects.filter(message_id=message_id).update(
                status='failed',
                results_summary={'error': str(e)},
            )
        return {'status': 'failed', 'reason': str(e)}
    finally:
        _db_connection.close()


def _run_evaluation_and_notify(zulip_msg, target, sites_data, duration, ticket_id=None):
    """
    Evaluate each site, send the Zulip message, update ZulipMessage.
    Shared by run_server_report (and safe to use from wait_for_completion_and_notify).
    """
    from .util.evaluator import SiteEvaluator
    from .utils import Notify

    warning_lines = []
    fail_lines = []

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

            baseline_snapshot = evaluator.baseline_snapshot
            latest_snapshot = evaluator.site.snapshots.first()

            status_unchanged = True
            ssim_bad = False

            if baseline_snapshot and latest_snapshot:
                if baseline_snapshot.http_status_code != latest_snapshot.http_status_code:
                    status_unchanged = False

            if evaluator.has_comparison() and evaluator.latest_comparison:
                ssim = evaluator.latest_comparison.ssim_score
                if ssim is not None and ssim < 0.90:
                    ssim_bad = True

            parts = text.split('|')
            if len(parts) >= 6:
                site_link   = parts[1].strip()
                status_part = parts[2].strip()
                ssim_part   = parts[3].strip()
                score_part  = parts[4].strip()
                change_part = parts[5].strip()
            else:
                site_link = site_name
                status_part = ssim_part = score_part = change_part = "-"

            if not status_unchanged:
                fail_lines.append(
                    f"| ❌ | {site_link} | {status_part} | {ssim_part} | {score_part} | {change_part} |"
                )
                failed_count += 1
                failed_sites_list.append(site_name)
            elif ssim_bad:
                warning_lines.append(
                    f"| ⚠️ | {site_link} | {status_part} | {ssim_part} | {score_part} | {change_part} |"
                )
                warning_count += 1
                warning_sites_list.append(site_name)
            else:
                successful_count += 1
        else:
            fail_lines.append(
                f"| ❌ {site_name} | Error: {evaluator.error} | - | - | - |"
            )
            failed_count += 1
            failed_sites_list.append(site_name)

    if failed_count > 0:
        final_status = 'failed'
    elif warning_count > 0:
        final_status = 'partial'
    else:
        final_status = 'completed'

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
            'duration_seconds': duration,
        }
        zulip_msg.save()

    target_name = target.get('name', 'Unknown')
    target_type = target.get('type', 'target')

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

    header = (
        f"📊 Monitoring Report for Server: {target_name}"
        if target_type == 'server'
        else f"📊 Monitoring Report for Domain: {target_name}"
    )

    if failed_count > 0:
        overall_emoji, overall_status_text = "❌", "FAILURES DETECTED"
    elif warning_count > 0:
        overall_emoji, overall_status_text = "⚠️", "WARNINGS DETECTED"
    else:
        overall_emoji, overall_status_text = "✅", "PASS"

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

    try:
        if target_type == 'server':
            title = f"[Server] {target_name}"
        else:
            title = f"[Domain] {target_name}"
        if "stage.town" in target_name.lower():
            Notify(target="stage").send(title=title, body=full_message)
        elif target_type == 'server':
            Notify(target="production").send(title=title, body=full_message)
        else:
            Notify(target="domain").send(title=title, body=full_message)
    except Exception as e:
        print(f"❌ Failed to send notification: {e}")
        print(full_message)

    return {
        'status': final_status,
        'successful': successful_count,
        'failed': failed_count,
        'warnings': warning_count,
        'duration': duration,
    }