# monitoring/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django_rq import get_queue
from django.db import transaction
from .models import Site, SiteSnapshot
from .tasks import capture_screenshot_task, create_comparison_task
import time
import inspect

@receiver(post_save, sender=Site)
def create_initial_snapshot(sender, instance, created, **kwargs):
    if created:
        # Quick check for caller
        caller = "unknown"
        for frame in inspect.stack():
            if 'handle_sites' in frame.function:
                caller = "api"
                break
            elif 'admin.py' in frame.filename:
                caller = "admin"
                break
            elif 'loaddata' in frame.function:
                caller = "management"
                break
        
        transaction.on_commit(lambda: _create_snapshot_if_needed(instance.id, caller))

def _create_snapshot_if_needed(site_id, caller="unknown"):
    try:
        site = Site.objects.get(id=site_id)
        
        time.sleep(0.1)
        
        existing_count = site.snapshots.count()
        if existing_count > 0:
            print(f"Site {site.name} already has {existing_count} snapshot(s) (triggered by {caller}) - skipping")
            return
        
        print(f"Site created via {caller}: {site.name} - creating initial snapshot")
        
        snapshot = SiteSnapshot.objects.create(
            site=site,
            http_status_code=0,
            content_length=0,
            is_baseline=True
        )
        
        print(f"Created snapshot ID: {snapshot.id}")
        
        print("Enqueuing screenshot task...")
        screenshot_job = capture_screenshot_task.delay(
            snapshot.id, 
            site.name, 
            site.id
        )
        print(f"Job ID: {screenshot_job.id}")
        
        print("Enqueuing comparison task...")
        comparison_job = create_comparison_task.delay(
            snapshot.id, 
            site.id
        )
        print(f"Job ID: {comparison_job.id}")
        
    except Site.DoesNotExist:
        print(f"Site {site_id} not found when creating snapshot")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()