import ipaddress
from apps.infrastructure.models import IPAddress, IPClass


def is_our_ip(ip: str | None) -> bool:
    """
    True if `ip` is registered as an explicit IPAddress row
    OR falls inside any IPClass.network range.
    """
    if not ip:
        return False

    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False

    # Explicitly registered IP
    if IPAddress.objects.filter(ip_address=ip).exists():
        return True

    # Inside any declared network
    for net_str in (
        IPClass.objects
        .exclude(network__isnull=True)
        .exclude(network="")
        .values_list("network", flat=True)
    ):
        try:
            net = ipaddress.ip_network(net_str, strict=False)
        except ValueError:
            continue
        if addr.version == net.version and addr in net:
            return True

    return False