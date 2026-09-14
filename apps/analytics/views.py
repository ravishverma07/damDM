from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET

from apps.core.utils import client_ip, hash_ip

from .models import LinkClick, TrackedLink


@require_GET
def link_redirect(request, slug: str):
    """
    Resolve a tracked link, record the click, and redirect.

    Only a salted hash of the IP is stored, never the address itself.
    """
    link = get_object_or_404(TrackedLink.objects.select_related("automation"), slug=slug)
    LinkClick.objects.create(
        tracked_link=link,
        recipient_scoped_id=request.GET.get("u", "")[:64],
        ip_hash=hash_ip(client_ip(request)),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:400],
    )
    link.register_click()
    return HttpResponseRedirect(link.destination_url)
