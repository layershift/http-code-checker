# monitoring/signals.py
from django.db.models.signals import post_save
from django.dispatch import receiver
from django_rq import get_queue
from rq import Queue
from redis import Redis
from django.conf import settings
from .models import Site, SiteSnapshot
from .tasks import capture_screenshot_task, create_comparison_task

@receiver(post_save, sender=Site)
def create_initial_snapshot(sender, instance, created, **kwargs):
    """
    Automatically create a snapshot when a new site is added
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
            
            # Method 1: Using delay() - should be async
            print("📤 Enqueuing screenshot task with delay()...")
            screenshot_job = capture_screenshot_task.delay(
                snapshot.id, 
                instance.name, 
                instance.id
            )
            print(f"   Job ID: {screenshot_job.id}")
            print(f"   Job status: {screenshot_job.get_status()}")
            
            # Method 2: Verify queue directly
            from django_rq import get_queue
            queue = get_queue('default')
            print(f"   Queue size after enqueue: {queue.count}")
            
            # Get the job from Redis to verify it exists
            from rq.job import Job
            try:
                redis_conn = queue.connection
                job_from_redis = Job.fetch(screenshot_job.id, connection=redis_conn)
                print(f"   Job found in Redis: {job_from_redis.id}")
                print(f"   Job function: {job_from_redis.func_name}")
            except Exception as e:
                print(f"   Job NOT found in Redis: {e}")
            
            # Enqueue comparison job
            print("📤 Enqueuing comparison task with delay()...")
            comparison_job = create_comparison_task.delay(
                snapshot.id, 
                instance.id
            )
            print(f"   Job ID: {comparison_job.id}")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
