from django.conf import settings
from django.db.models import Q

from .models import ImportBatch, PendingEntry, Transaction


def navigation(request):
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_verified()):
        return {}
    return {
        "nav_review_count": Transaction.objects.filter(Q(worktag__isnull=True) | Q(category__isnull=True)).count(),
        "nav_pending_count": PendingEntry.objects.filter(status=PendingEntry.Status.PENDING).count(),
        "nav_staged_count": ImportBatch.objects.filter(status=ImportBatch.Status.STAGED).count(),
    }


def app_version(request):
    version = settings.APP_VERSION
    return {
        "app_version": version.label,
        "app_commit": version.commit,
        "app_is_release": version.is_release,
    }
