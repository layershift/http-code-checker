# signals.py - Add this function and update the snapshot creation
import threading
import os
import tempfile
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.files.base import ContentFile
from django.utils import timezone
from .models import Site, SiteSnapshot, ScreenshotComparison
from .comparison import compare_screenshots
import cv2
import numpy as np

def capture_screenshot_thread(snapshot_id):
    """Thread function to capture screenshot"""
    from playwright.sync_api import sync_playwright
    from django.core.files.base import ContentFile
    from django.utils import timezone
    from .models import SiteSnapshot, ScreenshotComparison
    from .comparison import compare_screenshots
    
    temp_path = None
    
    try:
        # Get snapshot
        snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        print(f"Processing screenshot for {snapshot.site.name}")
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            temp_path = tmp_file.name

        url = snapshot.site.name
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url

        # Take screenshot
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            
            try:
                response = page.goto(url, wait_until="networkidle", timeout=30000)
                status_code = response.status if response else 500
                page.screenshot(path=temp_path, full_page=True)
                content = page.content()
                content_length = len(content.encode('utf-8'))
                
                # Read screenshot
                with open(temp_path, 'rb') as f:
                    screenshot_data = f.read()
                
                # Update snapshot
                snapshot.http_status_code = status_code
                snapshot.content_length = content_length
                
                if screenshot_data and status_code < 400:
                    filename = f"{snapshot.site.name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.png"
                    snapshot.screenshot.save(filename, ContentFile(screenshot_data), save=True)
                    
                    # NEW: Create comparison with previous snapshot
                    previous_snapshot = SiteSnapshot.objects.filter(
                        site=snapshot.site,
                        screenshot__isnull=False
                    ).exclude(id=snapshot.id).order_by('-taken_at').first()
                    
                    if previous_snapshot:
                        # Create temp directory for comparison images
                        with tempfile.TemporaryDirectory() as temp_dir:
                            # Compare screenshots
                            result = compare_screenshots(previous_snapshot, snapshot, output_dir=temp_dir)
                            
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
                            
                            # Save heatmap if generated
                            if result.get('heatmap_image_path') and os.path.exists(result['heatmap_image_path']):
                                with open(result['heatmap_image_path'], 'rb') as f:
                                    heatmap_data = f.read()
                                comparison.heatmap.save(
                                    f"heatmap_{previous_snapshot.id}_vs_{snapshot.id}.png",
                                    ContentFile(heatmap_data)
                                )
                            
                            # Save diff image if generated
                            if result.get('diff_image_path') and os.path.exists(result['diff_image_path']):
                                with open(result['diff_image_path'], 'rb') as f:
                                    diff_data = f.read()
                                comparison.diff_image.save(
                                    f"diff_{previous_snapshot.id}_vs_{snapshot.id}.png",
                                    ContentFile(diff_data)
                                )
                            
                            print(f"Created comparison: SSIM={result['ssim_score']:.4f}")
                    
                else:
                    snapshot.save()
                    
            except Exception as e:
                print(f"Browser error: {e}")
                snapshot.http_status_code = 500
                snapshot.content_length = 0
                snapshot.save()
            
            browser.close()
            
    except Exception as e:
        print(f"Critical error: {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
