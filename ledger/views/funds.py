import csv

from django.contrib import messages
from django.db import transaction as db_transaction
from django.db.models import Count, Max, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify

from ..forms import BudgetForm, WorktagForm
from ..models import ImportBatch, PendingEntry, Transaction, Worktag, WorktagType
from ..services.charts import burn_chart
from ..services.importer import rematch_transactions
from ..services.projections import Line, summarize_fund, summarize_funds
from ..templatetags.ledger_tags import money
from .common import confirm_page, csv_safe

LEVEL_ORDER = {"danger": 0, "warn": 1, "info": 2}


def _global_alerts() -> list[dict]:
    alerts = []
    unassigned = Transaction.objects.filter(worktag__isnull=True).aggregate(n=Count("id"), total=Sum("amount"))
    if n := unassigned["n"]:
        noun = "transaction isn't" if n == 1 else "transactions aren't"
        alerts.append({
            "level": "warn",
            "message": f"{n} {noun} assigned to a fund ({money(unassigned['total'])}).",
            "url": reverse("ledger:review"),
        })
    if n := Transaction.objects.filter(worktag__isnull=False, category__isnull=True).count():
        noun = "transaction needs" if n == 1 else "transactions need"
        alerts.append({"level": "warn", "message": f"{n} {noun} a category.", "url": reverse("ledger:review")})
    staged = ImportBatch.objects.filter(status=ImportBatch.Status.STAGED).count()
    if staged:
        alerts.append({"level": "info", "message": f"{staged} import{'s' if staged != 1 else ''} waiting for review.", "url": reverse("ledger:imports")})
    last = ImportBatch.objects.filter(status=ImportBatch.Status.COMMITTED).aggregate(last=Max("committed_at"))["last"]
    if last is None:
        alerts.append({"level": "info", "message": "No reports imported yet.", "url": reverse("ledger:imports")})
    elif (timezone.now() - last).days > 45:
        alerts.append({"level": "info", "message": f"Last import was {(timezone.now() - last).days} days ago.", "url": reverse("ledger:imports")})
    return alerts


def dashboard(request):
    show_all = request.GET.get("show") == "all"
    worktags = Worktag.objects.all() if show_all else Worktag.objects.filter(is_active=True)
    summaries = summarize_funds(worktags.prefetch_related("budget_lines"))
    groups = []
    for value, label in WorktagType.choices:
        items = [s for s in summaries if s.worktag.type == value]
        if items:
            groups.append((label, items))
    grand = Line(None)
    for s in summaries:
        for name in ("budget", "actual", "pending", "planned"):
            setattr(grand, name, getattr(grand, name) + getattr(s.total, name))
    fund_alerts = sorted(
        (alert for s in summaries for alert in s.alerts if alert.level != "info"),
        key=lambda alert: LEVEL_ORDER[alert.level],
    )
    context = {
        "groups": groups,
        "grand": grand,
        "fund_alerts": fund_alerts,
        "global_alerts": _global_alerts(),
        "show_all": show_all,
        "inactive_count": Worktag.objects.filter(is_active=False).count(),
    }
    return render(request, "ledger/dashboard.html", context)


def fund_detail(request, pk):
    worktag = get_object_or_404(Worktag, pk=pk)
    summary = summarize_fund(worktag)
    context = {
        "worktag": worktag,
        "summary": summary,
        "chart": burn_chart(summary),
        "transactions": worktag.transactions.select_related("category", "person")[:25],
        "transaction_count": worktag.transactions.count(),
        "pending": worktag.pending_entries.filter(status=PendingEntry.Status.PENDING).select_related("category", "person"),
    }
    return render(request, "ledger/fund_detail.html", context)


def fund_form(request, pk=None):
    worktag = get_object_or_404(Worktag, pk=pk) if pk else None
    initial = None
    if worktag is None:
        initial = {key: request.GET[key] for key in ("code", "name", "type") if request.GET.get(key)}
    form = WorktagForm(request.POST or None, instance=worktag, initial=initial)
    budget_form = BudgetForm(request.POST or None, worktag=worktag, prefix="budget")
    if request.method == "POST" and form.is_valid() and budget_form.is_valid():
        with db_transaction.atomic():
            worktag = form.save()
            budget_form.save(worktag)
        matched, _ = rematch_transactions()
        note = f" {matched} unassigned transactions matched to it." if matched else ""
        messages.success(request, f"Saved {worktag.code}.{note}")
        return redirect(worktag)
    return render(request, "ledger/fund_form.html", {"form": form, "budget_form": budget_form, "worktag": worktag})


def fund_delete(request, pk):
    worktag = get_object_or_404(Worktag, pk=pk)
    if request.method == "POST":
        moved = worktag.transactions.count()
        code = worktag.code
        worktag.delete()
        note = f" {moved} transactions moved to Review." if moved else ""
        messages.success(request, f"Deleted {code}.{note}")
        return redirect("ledger:dashboard")
    return confirm_page(
        request,
        title=f"Delete {worktag}?",
        detail="Its budget, pending entries, and planned appointments are deleted. Transactions are kept and move to Review.",
        cancel_url=worktag.get_absolute_url(),
    )


def fund_export(request, pk):
    worktag = get_object_or_404(Worktag, pk=pk)
    summary = summarize_fund(worktag)
    today = timezone.localdate()
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{slugify(worktag.code) or "fund"}-{today}.csv"'
    writer = csv.writer(response)
    writer.writerow(map(csv_safe, ["Fund", worktag.code, worktag.name]))
    writer.writerow(["Start", worktag.start_date or "", "End", worktag.end_date or "", "F&A rate %", worktag.fa_rate])
    writer.writerow(["Generated", today])
    writer.writerow([])
    writer.writerow(["Category", "Budget", "Actual", "Pending", "Planned", "Projected", "Remaining now", "Projected remaining"])
    for line in [*summary.lines, summary.total]:
        writer.writerow([
            csv_safe(line.category.name) if line.category else "Total",
            line.budget, line.actual, line.pending, line.planned, line.projected, line.remaining, line.projected_remaining,
        ])
    writer.writerow([])
    writer.writerow(["Date", "Amount", "Category", "Person", "Description", "Reference", "Source"])
    for txn in worktag.transactions.select_related("category", "person").order_by("date", "id"):
        writer.writerow([
            txn.date, txn.amount,
            *map(csv_safe, [
                txn.category.name if txn.category else "",
                txn.person.alias if txn.person else "",
                txn.description or txn.raw_category,
                txn.reference,
                txn.get_source_display(),
            ]),
        ])
    return response
