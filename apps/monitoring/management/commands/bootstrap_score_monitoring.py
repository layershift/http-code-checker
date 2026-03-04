# monitoring/management/commands/bootstrap_score_monitoring.py
from django.core.management.base import BaseCommand
from django_rq import get_queue
from apps.monitoring.models import Site
from apps.monitoring.tasks import has_other_pending_monitoring, monitor_site_score_task

class Command(BaseCommand):
    help = 'Bootstrap score monitoring for sites - enqueues initial tasks if none pending'

    def add_arguments(self, parser):
        parser.add_argument(
            '--site-id',
            type=int,
            help='Specific site ID to bootstrap (optional)'
        )

    def handle(self, *args, **options):
        self.stdout.write("🚀 Bootstrapping score monitoring...")
        
        # Get sites to monitor
        sites = Site.objects.filter(
            is_active=True,
            continuous_monitoring=True
        )
        
        if options['site_id']:
            sites = sites.filter(id=options['site_id'])
        
        queue = get_queue('monitoring')
        bootstrapped = 0
        skipped = 0
        
        for site in sites:
            # Check if already has pending monitoring (excluding any current job)
            if has_other_pending_monitoring(site.id, None):
                self.stdout.write(
                    self.style.WARNING(f"⏭️ Site {site.name} already has pending monitoring, skipping")
                )
                skipped += 1
                continue
            
            # Enqueue initial monitoring task
            job = queue.enqueue(
                monitor_site_score_task,
                site.id
            )
            
            self.stdout.write(
                self.style.SUCCESS(f"✅ Bootstrapped score monitoring for {site.name} (Job: {job.id})")
            )
            bootstrapped += 1
        
        self.stdout.write(
            self.style.SUCCESS(f"\n🎯 Complete: {bootstrapped} bootstrapped, {skipped} skipped")
        )