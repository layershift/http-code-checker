from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import IPClass, IPAddress
import ipaddress


@receiver(post_save, sender=IPClass)
def generate_ip_addresses(sender, instance, created, **kwargs):
    print("SIGNAL TRIGGERED")
    if created and instance.auto_generate:
        try:
            network = ipaddress.ip_network(instance.network, strict=False)

            # Prevent insane generation (enterprise safety)
            if network.num_addresses > 1024:
                return  # safety limit

            for ip in network.hosts():
                IPAddress.objects.create(
                    ip_class=instance,
                    ip_address=str(ip)
                )

        except ValueError:
            pass  # invalid network format
