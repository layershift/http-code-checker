# signals.py
import threading
import os
import tempfile
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import close_old_connections
import time
from .models import Site, SiteSnapshot

@receiver(post_save, sender=Site)
def create_initial_snapshot(sender, instance, created, **kwargs):
    if created:
        try:
            instance.resolve_ip()
            instance.save()
            snapshot = SiteSnapshot.objects.create(
                site=instance,
                http_status_code=0,
                content_length=0
            )
            
            print(f"Created snapshot {snapshot.id} for {instance.name}")

            # Start background thread for screenshot
            thread = threading.Thread(
                target=capture_screenshot_thread,
                args=(snapshot.id,)
            )
            thread.daemon = True
            thread.start()

        except Exception as e:
            print(f"Error creating snapshot: {e}")

def capture_screenshot_thread(snapshot_id):
    """Thread function to capture screenshot"""
    # Import inside thread
    from playwright.sync_api import sync_playwright
    
    temp_path = None
    
    try:
        # Close any existing DB connections and get fresh snapshot
        close_old_connections()
        
        # Get a fresh copy of the snapshot
        from .models import SiteSnapshot
        snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        print(f"Processing screenshot for {snapshot.site.name}")
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            temp_path = tmp_file.name

        # Prepare URL
        url = snapshot.site.name
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url
            
        print(f"Accessing URL: {url}")

        status_code = 500
        content_length = 0
        screenshot_data = None

        # Use sync Playwright
        with sync_playwright() as p:
            print("Launching browser...")
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu"]
            )
            
            page = browser.new_page(viewport={"width": 1920, "height": 1080})

            try:
                # Navigate to URL
                print(f"Navigating to {url}...")
                response = page.goto(url, wait_until="networkidle", timeout=30000)
                
                status_code = response.status if response else 500
                print(f"Got status code: {status_code}")
                
                # Take screenshot
                print("Taking screenshot...")
                page.screenshot(path=temp_path, full_page=True)
                
                # Get content
                content = page.content()
                content_length = len(content.encode('utf-8'))
                print(f"Content length: {content_length}")

                # Read screenshot file
                with open(temp_path, 'rb') as f:
                    screenshot_data = f.read()

            except Exception as e:
                print(f"Browser error: {e}")
                status_code = 500
                content_length = 0

            finally:
                browser.close()
                print("Browser closed")

        # Now update Django model - do this AFTER browser is closed
        # and in a separate try/except block
        try:
            print("Updating database...")
            
            # Refresh snapshot from DB to ensure it's still there
            snapshot.refresh_from_db()
            
            snapshot.http_status_code = status_code
            snapshot.content_length = content_length

            if screenshot_data and status_code and status_code < 400:
                filename = f"{snapshot.site.name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.png"
                print(f"Saving screenshot as: {filename}")
                
                # Save the screenshot
                snapshot.screenshot.save(
                    filename,
                    ContentFile(screenshot_data),
                    save=True
                )
                print("Screenshot saved successfully!")
            else:
                print(f"Saving snapshot without screenshot (status: {status_code})")
                snapshot.save()
                
        except Exception as db_error:
            print(f"Database error: {db_error}")
            
            # Try one more time with a fresh connection
            try:
                close_old_connections()
                from .models import SiteSnapshot
                snapshot = SiteSnapshot.objects.get(id=snapshot_id)
                snapshot.http_status_code = status_code
                snapshot.content_length = content_length
                snapshot.save()
                print("Recovery save successful")
            except Exception as final_error:
                print(f"Final recovery failed: {final_error}")

    except Exception as e:
        print(f"Critical error in thread: {e}")
        
    finally:
        # Clean up temp file
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
                print(f"Cleaned up temp file: {temp_path}")
            except:
                pass
