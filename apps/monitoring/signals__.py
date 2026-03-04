# monitoring/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django_rq import get_queue
from .models import Site, SiteSnapshot
from .tasks import capture_screenshot_task, create_comparison_task

@receiver(post_save, sender=Site)
def create_initial_snapshot(sender, instance, created, **kwargs):
    """
    Automatically create a snapshot when a new site is added
    Chain the comparison job to run after screenshot
    """
    if created:
        try:
            print(f"🏁 Site created: {instance.name}")
            
            # Create snapshot
            snapshot = SiteSnapshot.objects.create(
                site=instance,
                http_status_code=0,
                content_length=0
            )
            
            print(f"✅ Created snapshot ID: {snapshot.id}")
            
            # Get the default queue
            queue = get_queue('default')
            
            # Enqueue screenshot task
            screenshot_job = queue.enqueue(
                capture_screenshot_task,
                snapshot.id,
                instance.name,
                instance.id
            )
            
            print(f"🚀 Enqueued screenshot job: {screenshot_job.id}")
            
            # Enqueue comparison job to run AFTER screenshot job
            comparison_job = queue.enqueue(
                create_comparison_task,
                snapshot.id,
                instance.id,
                depends_on=screenshot_job  # This creates the dependency!
            )
            
            print(f"🔗 Enqueued comparison job: {comparison_job.id} (depends on {screenshot_job.id})")

        except Exception as e:
            print(f"❌ Error: {e}")
