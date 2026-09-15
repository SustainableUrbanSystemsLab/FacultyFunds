from django.contrib import messages
from django.db import transaction as db_transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import AppointmentForm, MergePersonForm, PersonForm
from ..models import Appointment, PendingEntry, Person, PersonKey, Transaction
from .common import confirm_page, safe_next


def people_list(request):
    people = Person.objects.select_related("person_type").annotate(
        appointment_count=Count("appointments", distinct=True),
        transaction_count=Count("transaction", distinct=True),
    )
    return render(request, "ledger/people_list.html", {"people": people})


def person_create(request):
    form = PersonForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        person = form.save()
        messages.success(request, f"Added {person.alias}.")
        return redirect(person)
    return render(request, "ledger/person_form.html", {"form": form})


def person_detail(request, pk):
    person = get_object_or_404(Person.objects.select_related("person_type"), pk=pk)
    form = PersonForm(request.POST or None, instance=person)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect(person)
    context = {
        "person": person,
        "form": form,
        "merge_form": MergePersonForm(person=person),
        "appointments": person.appointments.select_related("worktag"),
        "transactions": Transaction.objects.filter(person=person).select_related("worktag", "category")[:50],
        "key_count": person.keys.count(),
    }
    return render(request, "ledger/person_detail.html", context)


def person_delete(request, pk):
    person = get_object_or_404(Person, pk=pk)
    if request.method == "POST":
        alias = person.alias
        person.delete()
        messages.success(request, f"Deleted {alias}.")
        return redirect("ledger:people")
    return confirm_page(
        request,
        title=f"Delete {person.alias}?",
        detail="Their appointments are deleted and transactions are unlinked. A later import creates a new person.",
        cancel_url=person.get_absolute_url(),
    )


@require_POST
def person_merge(request, pk):
    person = get_object_or_404(Person, pk=pk)
    form = MergePersonForm(request.POST, person=person)
    if not form.is_valid():
        messages.error(request, "Choose who to merge into.")
        return redirect(person)
    target = form.cleaned_data["target"]
    with db_transaction.atomic():
        for model in (PersonKey, Transaction, PendingEntry, Appointment):
            model.objects.filter(person=person).update(person=target)
        if target.person_type is None and person.person_type is not None:
            target.person_type = person.person_type
            target.save(update_fields=["person_type"])
        alias = person.alias
        person.delete()
    messages.success(request, f"Merged {alias} into {target.alias}.")
    return redirect(target)


def appointment_form(request, pk=None):
    appointment = get_object_or_404(Appointment, pk=pk) if pk else None
    initial = None
    if appointment is None:
        initial = {"person": request.GET.get("person") or None, "worktag": request.GET.get("fund") or None}
    form = AppointmentForm(request.POST or None, instance=appointment, initial=initial)
    if request.method == "POST" and form.is_valid():
        appointment = form.save()
        messages.success(request, "Appointment saved.")
        return redirect(safe_next(request, appointment.person.get_absolute_url()))
    return render(
        request, "ledger/appointment_form.html", {"form": form, "appointment": appointment, "next": request.GET.get("next", "")}
    )


def appointment_delete(request, pk):
    appointment = get_object_or_404(Appointment.objects.select_related("person"), pk=pk)
    if request.method == "POST":
        person = appointment.person
        appointment.delete()
        messages.success(request, "Appointment deleted.")
        return redirect(person)
    return confirm_page(request, title=f"Delete appointment “{appointment}”?", cancel_url=appointment.person.get_absolute_url())
