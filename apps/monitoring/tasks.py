# monitoring/tasks.py
import os
import sys
import tempfile
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import close_old_connections
from rq import get_current_job
from playwright.sync_api import sync_playwright
from datetime import timedelta
from django_rq import job, get_queue
from rq import get_current_job
from rq.registry import StartedJobRegistry, ScheduledJobRegistry, FinishedJobRegistry


print("🔄 Loading tasks module...")

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
        'User-Agent': 'Chrome/145.0.0.0 (compatible; Layershift/StatusChecker)',
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
                        "--single-process",
                        "--js-flags=--max-old-space-size=512",
                        "--disable-setuid-sandbox",
                        "--no-first-run",
                        "--no-zygote",
                        "--disable-logging"
                    ]
                )
                # Create context with viewport and device scale factor
                context = browser.new_context(
                    viewport={'width': 800, 'height': 600},
                    device_scale_factor=1
                )
                print("✅ Browser launched")
                
                page = context.new_page()
                print("✅ Page created")
                
                # === Intercept requests to apply custom headers (including for redirects) ===
                def intercept_request(route, request):
                    """Intercept and modify ALL requests (including redirects)"""
                    
                    headers = request.headers
                    
                    # Create a new headers dict with our custom headers
                    modified_headers = {**headers, **browser_headers}
                    
                    # Reinject our headers
                    route.continue_(headers=modified_headers)
                
                # Register the interceptor for all requests
                page.route("**/*", intercept_request)
                
                # Add stealth script to hide automation
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
                    
                    # Read screenshot data
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
        
        # Update snapshot
        print("💾 Updating snapshot in database...")
        
        # Refresh snapshot to ensure it's still there
        try:
            snapshot.refresh_from_db()
        except:
            # If refresh fails, get a fresh copy
            close_old_connections()
            snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        
        snapshot.http_status_code = status_code
        snapshot.content_length = content_length
        
        if screenshot_data and status_code and status_code < 400:
            filename = f"site_{site_id}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.png"
            print(f"💾 Saving screenshot as: {filename}")
            snapshot.screenshot.save(filename, ContentFile(screenshot_data), save=True)
            screenshot_saved = True
            print(f"✅ Screenshot saved for snapshot {snapshot_id}")
            
            # ===== TRIGGER COMPARISON HERE =====
            print("🔍 Triggering comparison job...")
            from .tasks import create_comparison_task
            comparison_job = create_comparison_task.delay(snapshot_id, site_id)
            print(f"🚀 Enqueued comparison job: {comparison_job.id}")
            # ====================================
            score_job = calculate_site_score_task.delay(snapshot_id)
            print(f"📊 Enqueued site score job: {score_job.id}")
            print("⏳ Waiting for comparison and score jobs to complete...")
            
            from rq.job import Job
            from django_rq import get_queue
            import time
            
            queue = get_queue('default')
            connection = queue.connection
            
            jobs_to_wait = []
            if comparison_job:
                jobs_to_wait.append(comparison_job.id)
            if score_job:
                jobs_to_wait.append(score_job.id)
            
            max_wait = 300  # 5 minutes total wait
            waited = 0
            completed = 0
            
            while waited < max_wait and completed < len(jobs_to_wait):
                completed = 0
                for job_id in jobs_to_wait:
                    try:
                        job = Job.fetch(job_id, connection=connection)
                        status = job.get_status()
                        if status in ['finished', 'failed']:
                            completed += 1
                    except:
                        pass
                
                if completed < len(jobs_to_wait):
                    time.sleep(2)
                    waited += 2
            
            # Check final status
            if comparison_job:
                try:
                    job = Job.fetch(comparison_job.id, connection=connection)
                    print(f"✅ Comparison job finished with status: {job.get_status()}")
                except:
                    print(f"⚠️ Could not fetch comparison job status")
            
            if score_job:
                try:
                    job = Job.fetch(score_job.id, connection=connection)
                    print(f"✅ Score job finished with status: {job.get_status()}")
                except:
                    print(f"⚠️ Could not fetch score job status")
            
        else:
            snapshot.save()
            print(f"⚠️ Snapshot {snapshot_id} saved without screenshot (status: {status_code})")
        
        # Return result for dependent job
        return {
            'snapshot_id': snapshot_id,
            'site_id': site_id,
            'screenshot_saved': screenshot_saved,
            'status_code': status_code,
            'comparison_triggered': screenshot_saved  # Indicate if comparison was triggered
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


@job('comparison', result_ttl=3600)
def create_comparison_task(snapshot_id, site_id):
    """
    Task 2: Create comparison with baseline snapshot
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
            # If no baseline exists, make this the baseline if it's the first one
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
            
            # Compare screenshots
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
                previous_snapshot=baseline_snapshot,  # Always baseline as previous
                current_snapshot=current_snapshot,
                ssim_score=result['ssim_score'],
                percent_difference=result['percent_difference'],
                changed_pixels=result['changed_pixels'],
                total_pixels=result['total_pixels']
            )
            print(f"✅ Created comparison ID: {comparison.id}")
            
            # Save heatmap if generated
            if result.get('heatmap_image_path') and os.path.exists(result['heatmap_image_path']):
                with open(result['heatmap_image_path'], 'rb') as f:
                    heatmap_data = f.read()
                comparison.heatmap.save(
                    f"heatmap_baseline_vs_{current_snapshot.id}.png",
                    ContentFile(heatmap_data)
                )
                print(f"✅ Saved heatmap")
            
            # Save diff image if generated
            if result.get('diff_image_path') and os.path.exists(result['diff_image_path']):
                with open(result['diff_image_path'], 'rb') as f:
                    diff_data = f.read()
                comparison.diff_image.save(
                    f"diff_baseline_vs_{current_snapshot.id}.png",
                    ContentFile(diff_data)
                )
                print(f"✅ Saved diff image")
            
            print(f"✅ Comparison with baseline completed for snapshot {snapshot_id}")
            
            return {
                'snapshot_id': snapshot_id,
                'comparison_created': True,
                'comparison_id': comparison.id,
                'ssim_score': result['ssim_score'],
                'percent_difference': result['percent_difference'],
                'baseline_id': baseline_snapshot.id
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
    

import traceback

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


def has_pending_monitoring(site_id):
    """Legacy function - checks if any job exists (including current)"""
    return has_other_pending_monitoring(site_id, None)


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