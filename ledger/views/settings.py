from django.contrib import messages
from django.db import transaction as db_transaction
from django.db.models import ProtectedError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from ..forms import CategoryFormSet, CategoryRuleFormSet, FiscalYearRatesForm, PersonTypeFormSet
from ..models import Category, CategoryRule, FiscalYearRate, ImportProfile, PersonType
from ..services.importer import rematch_transactions
from ..services.rates import fiscal_year

FORMSETS = {
    "categories": (CategoryFormSet, "cat", lambda: Category.objects.all()),
    "person_types": (PersonTypeFormSet, "pt", lambda: PersonType.objects.all()),
    "rules": (CategoryRuleFormSet, "rule", lambda: CategoryRule.objects.select_related("category")),
}


def _fiscal_years(request, current: int) -> list[int]:
    """Years with saved rates, the current and next fiscal year, and any column the user added."""
    years = set(FiscalYearRate.objects.values_list("fiscal_year", flat=True)) | {current, current + 1}
    for raw in request.GET.getlist("fy") + request.POST.getlist("fy"):
        raw = raw.strip()
        if raw.isdigit() and 2000 <= int(raw) <= 2100:
            years.add(int(raw))
    return sorted(years)


def settings_page(request):
    bound = request.POST.get("which") if request.method == "POST" else None
    current_fy = fiscal_year(timezone.localdate())
    formsets = {
        key: cls(request.POST if key == bound else None, prefix=prefix, queryset=queryset())
        for key, (cls, prefix, queryset) in FORMSETS.items()
    }
    rates_form = FiscalYearRatesForm(
        request.POST if bound == "rates" else None,
        fiscal_years=_fiscal_years(request, current_fy),
        person_types=PersonType.objects.all(),
    )
    if bound == "rates":
        if rates_form.is_valid():
            rates_form.save()
            messages.success(request, "Rates saved.")
            return redirect(reverse("ledger:settings") + "#rates")
        messages.error(request, "Fix the highlighted rates.")
    elif bound in formsets:
        formset = formsets[bound]
        if formset.is_valid():
            if bound == "categories" and any(f.instance.pk and f.instance.is_system for f in formset.deleted_forms):
                messages.error(request, "Built-in categories can't be deleted.")
            else:
                try:
                    with db_transaction.atomic():
                        formset.save()
                except ProtectedError:
                    messages.error(request, "That category is in use. Reassign its transactions and budget lines first.")
                else:
                    message = "Saved."
                    if bound == "rules":
                        _, categorized = rematch_transactions()
                        if categorized:
                            message += f" {categorized} uncategorized transactions now have a category."
                    messages.success(request, message)
                    return redirect(reverse("ledger:settings") + f"#{bound}")
        else:
            messages.error(request, "Fix the highlighted fields.")
    context = {
        "category_formset": formsets["categories"],
        "person_type_formset": formsets["person_types"],
        "rule_formset": formsets["rules"],
        "rates_form": rates_form,
        "current_fy": current_fy,
        "profiles": ImportProfile.objects.all(),
    }
    return render(request, "ledger/settings.html", context)
