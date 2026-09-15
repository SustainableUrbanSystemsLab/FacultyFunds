from django.contrib import messages
from django.db import transaction as db_transaction
from django.db.models import ProtectedError
from django.shortcuts import redirect, render
from django.urls import reverse

from ..forms import CategoryFormSet, CategoryRuleFormSet, PersonTypeFormSet
from ..models import Category, CategoryRule, ImportProfile, PersonType
from ..services.importer import rematch_transactions

FORMSETS = {
    "categories": (CategoryFormSet, "cat", lambda: Category.objects.all()),
    "person_types": (PersonTypeFormSet, "pt", lambda: PersonType.objects.all()),
    "rules": (CategoryRuleFormSet, "rule", lambda: CategoryRule.objects.select_related("category")),
}


def settings_page(request):
    bound = request.POST.get("which") if request.method == "POST" else None
    formsets = {
        key: cls(request.POST if key == bound else None, prefix=prefix, queryset=queryset())
        for key, (cls, prefix, queryset) in FORMSETS.items()
    }
    if bound in formsets:
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
        "profiles": ImportProfile.objects.all(),
    }
    return render(request, "ledger/settings.html", context)
