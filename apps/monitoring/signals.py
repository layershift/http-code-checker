# signals.py
import threading
import os
import tempfile
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import close_old_connections
from .models import Site, SiteSnapshot, ScreenshotComparison
from .comparison import compare_screenshots
import asyncio

@receiver(post_save, sender=Site)
def create_initial_snapshot(sender, instance, created, **kwargs):
    """
    Automatically create a snapshot when a new site is added
    """
    if created:
        try:
            print(f"🏁 Site created: {instance.name} - creating initial snapshot")
            
            # Create snapshot
            snapshot = SiteSnapshot.objects.create(
                site=instance,
                http_status_code=0,
                content_length=0
            )
            
            print(f"✅ Created snapshot ID: {snapshot.id} for {instance.name}")

            # Start background thread for screenshot
            thread = threading.Thread(
                target=capture_screenshot_thread,
                args=(snapshot.id, instance.name, instance.id)  # Pass site data directly
            )
            thread.daemon = True
            thread.start()
            print(f"🚀 Started screenshot thread for snapshot {snapshot.id}")

        except Exception as e:
            print(f"❌ Error creating snapshot: {e}")

def capture_screenshot_thread(snapshot_id, site_name, site_id):
    """Thread function to capture screenshot - receives site data directly"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(async_capture_screenshot(snapshot_id, site_name, site_id))
    finally:
        loop.close()
        close_old_connections()

async def async_capture_screenshot(snapshot_id, site_name, site_id):
    """Async function to capture screenshot - uses passed site data"""
    from playwright.async_api import async_playwright
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from asgiref.sync import sync_to_async
    from .models import SiteSnapshot, ScreenshotComparison
    from .comparison import compare_screenshots
    import os
    import tempfile

    temp_path = None

    try:
        # Wrap database GET in sync_to_async - but we already have site data
        @sync_to_async
        def get_snapshot():
            # Use select_related to fetch site in the same query
            return SiteSnapshot.objects.select_related('site').get(id=snapshot_id)
        
        @sync_to_async
        def save_snapshot(snapshot, status_code, content_length, screenshot_data=None):
            snapshot.http_status_code = status_code
            snapshot.content_length = content_length
            
            if screenshot_data:
                # Use site_id from parameters
                timestamp = timezone.now().strftime('%Y%m%d_%H%M%S')
                filename = f"site_{site_id}_{timestamp}.png"
                snapshot.screenshot.save(filename, ContentFile(screenshot_data), save=True)
            else:
                snapshot.save()
            return snapshot
        
        @sync_to_async
        def get_previous_snapshot(current_id):
            return SiteSnapshot.objects.filter(
                site_id=site_id,  # Use site_id directly
                screenshot__isnull=False
            ).exclude(id=current_id).order_by('-taken_at').first()
        
        @sync_to_async
        def create_comparison(previous, current, result):
            comparison = ScreenshotComparison.objects.create(
                site_id=site_id,  # Use site_id directly
                previous_snapshot=previous,
                current_snapshot=current,
                ssim_score=result['ssim_score'],
                percent_difference=result['percent_difference'],
                changed_pixels=result['changed_pixels'],
                total_pixels=result['total_pixels']
            )
            return comparison
        
        @sync_to_async
        def save_comparison_image(comparison, image_data, image_type, prev_id, curr_id):
            if image_type == 'heatmap':
                comparison.heatmap.save(
                    f"heatmap_{prev_id}_vs_{curr_id}.png",
                    ContentFile(image_data)
                )
            elif image_type == 'diff':
                comparison.diff_image.save(
                    f"diff_{prev_id}_vs_{curr_id}.png",
                    ContentFile(image_data)
                )
        
        # Get snapshot with prefetched site data
        snapshot = await get_snapshot()
        print(f"📸 Processing screenshot for {site_name}")  # Use passed site_name

        # Create temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            temp_path = tmp_file.name

        # Prepare URL - use passed site_name
        url = site_name
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url

        print(f"🌐 Accessing URL: {url}")

        # Take screenshot with async Playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, 
                args=["--no-sandbox", "--disable-gpu"]
            )
            page = await browser.new_page(viewport={"width": 1920, "height": 1080})

            try:
                response = await page.goto(url, wait_until="networkidle", timeout=30000)
                status_code = response.status if response else 500
                print(f"✅ Got status code: {status_code}")
                
                await page.screenshot(path=temp_path, full_page=True)
                print(f"✅ Screenshot taken")
                
                content = await page.content()
                content_length = len(content.encode('utf-8'))
                print(f"📊 Content length: {content_length}")

                # Read screenshot
                with open(temp_path, 'rb') as f:
                    screenshot_data = f.read()

                # Save to database using sync_to_async
                if screenshot_data and status_code < 400:
                    await save_snapshot(snapshot, status_code, content_length, screenshot_data)
                    print(f"✅ Screenshot saved")

                    # Get previous snapshot
                    previous_snapshot = await get_previous_snapshot(snapshot.id)

                    if previous_snapshot:
                        print(f"🔍 Creating comparison with previous snapshot ID: {previous_snapshot.id}")
                        
                        # Create temp directory for comparison images
                        with tempfile.TemporaryDirectory() as temp_dir:
                            # Compare screenshots (this is synchronous)
                            result = compare_screenshots(previous_snapshot, snapshot, output_dir=temp_dir)

                            if result['ssim_score'] is not None:
                                # Create comparison object
                                comparison = await create_comparison(
                                    previous_snapshot, 
                                    snapshot, 
                                    result
                                )
                                print(f"✅ Created comparison: SSIM={result['ssim_score']:.4f}")

                                # Save heatmap if generated
                                if result.get('heatmap_image_path') and os.path.exists(result['heatmap_image_path']):
                                    with open(result['heatmap_image_path'], 'rb') as f:
                                        heatmap_data = f.read()
                                    await save_comparison_image(
                                        comparison, 
                                        heatmap_data, 
                                        'heatmap',
                                        previous_snapshot.id,
                                        snapshot.id
                                    )
                                    print(f"✅ Saved heatmap")

                                # Save diff image if generated
                                if result.get('diff_image_path') and os.path.exists(result['diff_image_path']):
                                    with open(result['diff_image_path'], 'rb') as f:
                                        diff_data = f.read()
                                    await save_comparison_image(
                                        comparison, 
                                        diff_data, 
                                        'diff',
                                        previous_snapshot.id,
                                        snapshot.id
                                    )
                                    print(f"✅ Saved diff image")
                            else:
                                print(f"❌ Comparison failed - no results")
                    else:
                        print(f"📭 No previous snapshot found for comparison")
                else:
                    await save_snapshot(snapshot, status_code, content_length)
                    print(f"💾 Saved snapshot without screenshot (status: {status_code})")

            except Exception as e:
                print(f"❌ Browser error: {e}")
                await save_snapshot(snapshot, 500, 0)

            finally:
                await browser.close()
                print(f"✅ Browser closed")

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
        
        print(f"🏁 Screenshot capture completed for snapshot {snapshot_id}")
