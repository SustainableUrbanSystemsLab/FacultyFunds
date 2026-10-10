from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from ..forms import BulkAssignForm, TransactionForm
from ..models import Category, Source, Transaction, Worktag
from ..services.importer import rematch_transactions
from ..services.rules import WorktagMatcher, describe_code
from .common import confirm_page, safe_next

PAGE_SIZE = 100


def _safe_date(value):
    try:
        return parse_date(value or "")
    except ValueError:
        return None


def _filtered(params):
    qs = Transaction.objects.select_related("worktag", "category", "person")
    if q := params.get("q", "").strip():
        qs = qs.filter(
            Q(description__icontains=q) | Q(reference__icontains=q) | Q(raw_category__icontains=q) | Q(raw_worktags__icontains=q)
        )
    for param, field in (("fund", "worktag"), ("category", "category"), ("person", "person")):
        value = params.get(param, "")
        if value == "none":
            qs = qs.filter(**{f"{field}__isnull": True})
        elif value.isdigit():
            qs = qs.filter(**{f"{field}_id": int(value)})
    if source := params.get("source"):
        qs = qs.filter(source=source)
    if (batch := params.get("batch", "")).isdigit():
        qs = qs.filter(batch_id=int(batch))
    if date_from := _safe_date(params.get("from")):
        qs = qs.filter(date__gte=date_from)
    if date_to := _safe_date(params.get("to")):
        qs = qs.filter(date__lte=date_to)
    return qs


def transaction_list(request):
    qs = _filtered(request.GET)
    page = Paginator(qs, PAGE_SIZE).get_page(request.GET.get("page"))
    context = {
        "page": page,
        "total": qs.aggregate(total=Sum("amount"))["total"] or 0,
        "funds": Worktag.objects.all(),
        "categories": Category.objects.all(),
        "sources": Source.choices,
        "bulk_form": BulkAssignForm(),
        "params": request.GET,
    }
    return render(request, "ledger/transaction_list.html", context)


def transaction_form(request, pk=None):
    txn = get_object_or_404(Transaction, pk=pk) if pk else None
    initial = None
    if txn is None:
        initial = {"worktag": request.GET.get("fund") or None}
    form = TransactionForm(request.POST or None, instance=txn, initial=initial)
    next_url = safe_next(request, "ledger:transactions")
    if request.method == "POST" and form.is_valid():
        saved = form.save(commit=False)
        if txn is None:
            saved.source = Source.MANUAL
        saved.save()
        messages.success(request, "Transaction saved.")
        return redirect(next_url)
    return render(request, "ledger/transaction_form.html", {"form": form, "txn": txn, "next": next_url})


def transaction_delete(request, pk):
    txn = get_object_or_404(Transaction, pk=pk)
    if request.method == "POST":
        txn.delete()
        messages.success(request, "Transaction deleted.")
        return redirect(safe_next(request, "ledger:transactions"))
    detail = "If it came from an import, importing the same file again will add it back."
    return confirm_page(request, title=f"Delete transaction “{txn}”?", detail=detail, cancel_url=safe_next(request, "ledger:transactions"))


@require_POST
def transactions_bulk(request):
    next_url = safe_next(request, "ledger:transactions")
    ids = [int(value) for value in request.POST.getlist("ids") if value.isdigit()]
    if not ids:
        messages.warning(request, "Select at least one transaction.")
        return redirect(next_url)
    qs = Transaction.objects.filter(pk__in=ids)
    if request.POST.get("action") == "delete":
        count = qs.count()
        qs.delete()
        messages.success(request, f"Deleted {count} transactions.")
        return redirect(next_url)
    form = BulkAssignForm(request.POST)
    if form.is_valid():
        updates = {key: value for key, value in form.cleaned_data.items() if value}
        if updates:
            count = qs.update(**updates)
            messages.success(request, f"Updated {count} transactions.")
        else:
            messages.warning(request, "Choose a fund or a category to set.")
    return redirect(next_url)


def review(request):
    unassigned = Transaction.objects.filter(worktag__isnull=True).select_related("category", "person")
    uncategorized = Transaction.objects.filter(worktag__isnull=False, category__isnull=True).select_related("worktag", "person")
    matcher = WorktagMatcher(list(Worktag.objects.all()))
    codes: dict[str, dict] = {}
    for raw in unassigned.values_list("raw_worktags", flat=True)[:5000]:
        for code in matcher.unknown_codes(raw):
            entry = codes.setdefault(code, {"code": code, "count": 0, "type": "", "name": ""})
            entry["count"] += 1
            if not entry["name"]:
                entry["type"], entry["name"] = describe_code(code, raw)
    context = {
        "unassigned": unassigned[:300],
        "unassigned_count": unassigned.count(),
        "uncategorized": uncategorized[:300],
        "uncategorized_count": uncategorized.count(),
        "suggestions": sorted(codes.values(), key=lambda entry: -entry["count"])[:30],
        "bulk_form": BulkAssignForm(),
    }
    return render(request, "ledger/review.html", context)


@require_POST
def review_rematch(request):
    funds, categories = rematch_transactions()
    messages.success(request, f"Matched {funds} transactions to funds and {categories} to categories.")
    return redirect("ledger:review")
