from django.db.models import Q

from .models import ImportBatch, Transaction


def navigation(request):
    user = getattr(request, "user", None)
    if not (user and user.is_authenticated and user.is_verified()):
        return {}
    return {
        "nav_review_count": Transaction.objects.filter(Q(worktag__isnull=True) | Q(category__isnull=True)).count(),
        "nav_staged_count": ImportBatch.objects.filter(status=ImportBatch.Status.STAGED).count(),
    }
