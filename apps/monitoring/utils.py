# monitoring/utils.py - Updated with better logging
import os
import tempfile
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import close_old_connections
from playwright.sync_api import sync_playwright
import apprise


def capture_screenshot_for_snapshot(snapshot_id):
    """
    Thread function to capture screenshot for a snapshot
    """
    print(f"🔍 Starting screenshot capture for snapshot ID: {snapshot_id}")
    close_old_connections()
    
    temp_path = None

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
        
        # Get the snapshot
        snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        print(f"✅ Found snapshot for site: {snapshot.site.name}")
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            temp_path = tmp_file.name
            print(f"📁 Created temp file: {temp_path}")

        # Prepare URL
        url = snapshot.site.name
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
        print(f"🌐 Accessing URL: {url}")

        status_code = 500
        content_length = 0
        screenshot_data = None

        # Take screenshot with Playwright
        print("🚀 Launching Playwright...")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox", 
                    "--disable-gpu",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=VizDisplayCompositor",
                    "--disable-dev-shm-usage",
                    "--disable-setuid-sandbox",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-logging"
                ]
                )
            
            context = browser.new_context(extra_http_headers=browser_headers,viewport={'width': 800, 'height': 600})
            print("✅ Browser launched")

            page = context.browser.new_page(viewport={'width': 800, 'height': 600})
            print("✅ Page created")

            try:
                print(f"⏳ Navigating to {url}...")
                response = page.goto(url, wait_until="networkidle", timeout=30000)
                status_code = response.status if response else 500
                print(f"✅ Got status code: {status_code}")
                
                print("📸 Taking screenshot...")
                page.screenshot(path=temp_path, full_page=True)
                print("✅ Screenshot taken")
                
                content = page.content()
                content_length = len(content.encode('utf-8'))
                print(f"📊 Content length: {content_length}")

                with open(temp_path, 'rb') as f:
                    screenshot_data = f.read()
                print(f"💾 Screenshot size: {len(screenshot_data)} bytes")

            except Exception as e:
                print(f"❌ Browser error: {e}")
                status_code = 500
                content_length = 0
                
            finally:
                browser.close()
                print("✅ Browser closed")

        # Update snapshot
        print("💾 Updating snapshot in database...")
        snapshot.http_status_code = status_code
        snapshot.content_length = content_length

        if screenshot_data and status_code and status_code < 400:
            filename = f"{snapshot.site.name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.png"
            print(f"💾 Saving screenshot as: {filename}")
            snapshot.screenshot.save(
                filename,
                ContentFile(screenshot_data),
                save=True
            )
            print("✅ Screenshot saved to database")
        else:
            print(f"💾 Saving snapshot without screenshot (status: {status_code})")
            snapshot.save()

        # 🔥 NEW: Create comparison with previous snapshot
        print("🔍 Checking for previous snapshot to compare...")
        previous_snapshot = SiteSnapshot.objects.filter(
            site=snapshot.site,
            screenshot__isnull=False
        ).exclude(id=snapshot.id).order_by('-taken_at').first()

        if previous_snapshot:
            print(f"✅ Found previous snapshot ID: {previous_snapshot.id} from {previous_snapshot.taken_at}")
            
            try:
                # Import comparison function
                from .comparison import compare_screenshots
                
                # Create temp directory for comparison images
                with tempfile.TemporaryDirectory() as temp_dir:
                    print(f"📁 Created temp dir for comparison: {temp_dir}")
                    
                    # Compare screenshots
                    result = compare_screenshots(previous_snapshot, snapshot, output_dir=temp_dir)
                    
                    if result['ssim_score'] is not None:
                        print(f"📊 Comparison results: SSIM={result['ssim_score']:.4f}, "
                              f"Diff={result['percent_difference']:.2f}%")
                        
                        # Create ScreenshotComparison object
                        comparison = ScreenshotComparison.objects.create(
                            site=snapshot.site,
                            previous_snapshot=previous_snapshot,
                            current_snapshot=snapshot,
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
                                f"heatmap_{previous_snapshot.id}_vs_{snapshot.id}.png",
                                ContentFile(heatmap_data)
                            )
                            print("✅ Saved heatmap")
                        
                        # Save diff image if generated
                        if result.get('diff_image_path') and os.path.exists(result['diff_image_path']):
                            with open(result['diff_image_path'], 'rb') as f:
                                diff_data = f.read()
                            comparison.diff_image.save(
                                f"diff_{previous_snapshot.id}_vs_{snapshot.id}.png",
                                ContentFile(diff_data)
                            )
                            print("✅ Saved diff image")
                    else:
                        print("❌ Comparison failed - no results")
                        
            except Exception as e:
                print(f"❌ Error creating comparison: {e}")
                import traceback
                traceback.print_exc()
        else:
            print("📭 No previous snapshot found for comparison")
            
    except Exception as e:
        print(f"❌ Critical error in screenshot thread: {e}")
        import traceback
        traceback.print_exc()
        
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
                print(f"🧹 Cleaned up temp file: {temp_path}")
            except:
                pass
        
        close_old_connections()
        print("🏁 Screenshot capture completed")


class Notify:
    """
    Abstracts notification and associated setup stuff out of the way
    """

    apobj = apprise.Apprise()
    apobj.add(os.getenv('NOTIFICATION_URL', '')) 

    @classmethod
    def send(cls, title, body):
        cls.apobj.notify(title=title, body=body)