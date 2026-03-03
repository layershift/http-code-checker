# monitoring/utils.py
import os
import tempfile
import threading
from django.core.files.base import ContentFile
from django.utils import timezone
from django.db import close_old_connections
from playwright.sync_api import sync_playwright

def capture_screenshot_for_snapshot(snapshot_id):
    """
    Thread function to capture screenshot for a snapshot
    """
    # Close any existing connections before starting thread work
    close_old_connections()
    
    temp_path = None
    
    try:
        # Import inside thread to avoid circular imports
        from .models import SiteSnapshot
        
        # Get the snapshot with a fresh connection
        snapshot = SiteSnapshot.objects.get(id=snapshot_id)
        print(f"Processing screenshot for {snapshot.site.name}")
        
        # Create temp file
        with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
            temp_path = tmp_file.name

        # Prepare URL
        url = snapshot.site.name
        if not url.startswith(('http://', 'https://')):
            url = 'http://' + url

        status_code = 500
        content_length = 0
        screenshot_data = None

        # Use sync Playwright - this part is fine
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu"]
            )

            page = browser.new_page(viewport={"width": 1920, "height": 1080})

            try:
                # Navigate to URL
                response = page.goto(url, wait_until="networkidle", timeout=30000)
                status_code = response.status if response else 500
                
                # Take screenshot
                page.screenshot(path=temp_path, full_page=True)
                
                # Get content
                content = page.content()
                content_length = len(content.encode('utf-8'))

                # Read screenshot file
                with open(temp_path, 'rb') as f:
                    screenshot_data = f.read()
                    
            except Exception as e:
                print(f"Browser error for {url}: {e}")
                status_code = 500
                content_length = 0
                
            finally:
                browser.close()

        # NOW update Django model - with fresh connection
        try:
            # Close old connections and get fresh snapshot
            close_old_connections()
            
            # Get a fresh copy of the snapshot
            from .models import SiteSnapshot
            snapshot = SiteSnapshot.objects.get(id=snapshot_id)
            
            # Update fields
            snapshot.http_status_code = status_code
            snapshot.content_length = content_length

            # Save screenshot if we have one
            if screenshot_data and status_code and status_code < 400:
                filename = f"{snapshot.site.name}_{timezone.now().strftime('%Y%m%d_%H%M%S')}.png"
                print(f"Saving screenshot as: {filename}")
                
                # This operation should be fine as it's in the same thread
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
            
            # One more try with a completely new connection
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
        print(f"Critical error in screenshot thread: {e}")
        
    finally:
        # Clean up temp file
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
                print(f"Cleaned up temp file: {temp_path}")
            except:
                pass
        
        # Close connections at the end
        close_old_connections()
