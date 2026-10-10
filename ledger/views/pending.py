import datetime as dt

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from ..forms import PendingEntryForm
from ..models import PendingEntry, Transaction, Worktag
from .common import confirm_page, safe_next


def pending_list(request):
    status = request.GET.get("status", PendingEntry.Status.PENDING)
    entries = PendingEntry.objects.select_related("worktag", "category", "person", "reconciled_with")
    if status in PendingEntry.Status.values:
        entries = entries.filter(status=status)
    if (fund := request.GET.get("fund", "")).isdigit():
        entries = entries.filter(worktag_id=int(fund))
    context = {
        "entries": entries[:500],
        "status": status,
        "statuses": PendingEntry.Status.choices,
        "funds": Worktag.objects.all(),
        "params": request.GET,
        "today": timezone.localdate(),
    }
    return render(request, "ledger/pending_list.html", context)


def pending_form(request, pk=None):
    entry = get_object_or_404(PendingEntry, pk=pk) if pk else None
    initial = {"worktag": request.GET.get("fund") or None} if entry is None else None
    form = PendingEntryForm(request.POST or None, instance=entry, initial=initial)
    next_url = safe_next(request, "ledger:pending")
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Pending entry saved.")
        return redirect(next_url)
    return render(request, "ledger/pending_form.html", {"form": form, "entry": entry, "next": next_url})


def pending_delete(request, pk):
    entry = get_object_or_404(PendingEntry, pk=pk)
    if request.method == "POST":
        entry.delete()
        messages.success(request, "Pending entry deleted.")
        return redirect("ledger:pending")
    return confirm_page(request, title=f"Delete pending entry “{entry}”?", cancel_url=safe_next(request, "ledger:pending"))


def pending_reconcile(request, pk):
    entry = get_object_or_404(PendingEntry.objects.select_related("worktag", "category"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action in ("cancel", "reopen"):
            entry.status = PendingEntry.Status.CANCELLED if action == "cancel" else PendingEntry.Status.PENDING
            entry.reconciled_with = None
            entry.save(update_fields=["status", "reconciled_with"])
            messages.success(request, "Pending entry cancelled." if action == "cancel" else "Pending entry reopened.")
            return redirect("ledger:pending")
        txn_id = request.POST.get("transaction", "")
        if txn_id.isdigit():
            entry.reconciled_with = get_object_or_404(Transaction, pk=int(txn_id))
            entry.status = PendingEntry.Status.RECONCILED
            entry.save(update_fields=["status", "reconciled_with"])
            messages.success(request, "Reconciled: the actual transaction now replaces the pending amount.")
            return redirect("ledger:pending")
        messages.warning(request, "Choose a matching transaction.")

    candidates = Transaction.objects.filter(worktag=entry.worktag).exclude(
        reconciled_pending__status=PendingEntry.Status.RECONCILED
    )
    if entry.expected_date:
        window = dt.timedelta(days=120)
        candidates = candidates.filter(date__range=(entry.expected_date - window, entry.expected_date + window))
    anchor = entry.expected_date
    suggestions = sorted(
        candidates.select_related("category", "person")[:2000],
        key=lambda t: (abs(t.amount - entry.amount), abs((t.date - anchor).days) if anchor else 0),
    )[:15]
    return render(request, "ledger/pending_reconcile.html", {"entry": entry, "suggestions": suggestions})
