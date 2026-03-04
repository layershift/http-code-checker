# monitoring/tasks.py
import os
import tempfile
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import close_old_connections
from django_rq import job
from rq import get_current_job
from playwright.sync_api import sync_playwright

print("🔄 Loading tasks module...")

@job('default')
@job('default')
def capture_screenshot_task(snapshot_id, site_name, site_id):
    """
    Task 1: Capture screenshot for a snapshot - then trigger comparison
    """
    current_job = get_current_job()
    print(f"🎯 [Job {current_job.id}] Starting screenshot capture for snapshot {snapshot_id}, site: {site_name}")
    
    close_old_connections()
    temp_path = None
    screenshot_saved = False
    
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
                    args=["--no-sandbox", "--disable-gpu"]
                )
                print("✅ Browser launched")
                
                page = browser.new_page(viewport={"width": 1920, "height": 1080})
                print("✅ Page created")
                
                try:
                    print(f"⏳ Navigating to {url}...")
                    response = page.goto(url, wait_until="networkidle", timeout=30000)
                    status_code = response.status if response else 500
                    print(f"✅ Got status code: {status_code}")
                    
                    print("📸 Taking screenshot...")
                    page.screenshot(path=temp_path, full_page=True)
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


@job('default')
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