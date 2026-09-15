from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from ..forms import MappingForm, UploadForm
from ..models import ImportBatch, ImportProfile
from ..services import importer
from ..services.fileparse import FileParseError
from ..services.mapping import column_letter

PREVIEW_ROWS = 300
SAMPLE_ROWS = 8


def imports_index(request):
    importer.purge_stale_batches()
    context = {
        "form": UploadForm(),
        "batches": ImportBatch.objects.select_related("profile")[:50],
        "profiles": ImportProfile.objects.all(),
    }
    return render(request, "ledger/imports.html", context)


@require_POST
def import_upload(request):
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("ledger:imports")
    upload = form.cleaned_data["file"]
    try:
        batch = importer.stage_upload(upload.name, upload.read(), form.cleaned_data["source"])
    except FileParseError as exc:
        messages.error(request, str(exc))
        return redirect("ledger:imports")
    if batch.profile:
        messages.info(request, f"Used your saved column mapping “{batch.profile.name}”. Check the preview.")
        return redirect("ledger:import_preview", pk=batch.pk)
    return redirect("ledger:import_map", pk=batch.pk)


def _staged(pk):
    return get_object_or_404(ImportBatch, pk=pk, status=ImportBatch.Status.STAGED)


def import_map(request, pk):
    batch = _staged(pk)
    headers = importer.batch_headers(batch)
    if request.method == "POST":
        form = MappingForm(request.POST, batch=batch, headers=headers)
        sheet = request.POST.get("sheet_name", batch.sheet_name)
        header_row = request.POST.get("header_row", "")
        layout_changed = sheet != batch.sheet_name or (header_row.isdigit() and int(header_row) - 1 != batch.header_row)
        if layout_changed and sheet in batch.sheet_names:
            new_row = int(header_row) - 1 if header_row.isdigit() and sheet == batch.sheet_name else None
            importer.configure_sheet(batch, sheet, new_row)
            batch.save()
            messages.info(request, "Re-read the column names. Check the mapping below.")
            return redirect("ledger:import_map", pk=batch.pk)
        if form.is_valid():
            batch.column_map = form.column_map()
            batch.options = {**batch.options, **form.options()}
            if scrubbed := importer.ensure_person_column_scrubbed(batch):
                messages.info(request, f"“{scrubbed}” wasn't recognized as a people column at upload; its values are now pseudonymized.")
            batch.save(update_fields=["column_map", "options", "sheets", "person_tokens"])
            return redirect("ledger:import_preview", pk=batch.pk)
    else:
        initial = {
            "sheet_name": batch.sheet_name,
            "header_row": batch.header_row + 1,
            **{f"map_{key}": value for key, value in batch.column_map.items()},
            **batch.options,
        }
        form = MappingForm(initial=initial, batch=batch, headers=headers)

    rows = batch.rows
    start = max(0, batch.header_row - 2)
    sample = [(index + 1, row) for index, row in enumerate(rows[start : batch.header_row + 1 + SAMPLE_ROWS], start=start)]
    context = {
        "batch": batch,
        "form": form,
        "sample": sample,
        "columns": [column_letter(i) for i in range(len(headers))],
        "person_columns": importer.person_columns(batch),
        "row_count": max(0, len(rows) - batch.header_row - 1),
    }
    return render(request, "ledger/import_map.html", context)


def import_preview(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk)
    if batch.status != ImportBatch.Status.STAGED:
        return render(request, "ledger/import_done.html", {"batch": batch})
    records, summary = importer.parse_batch(batch)
    labels = importer.people_labels(batch)
    shown = records[:PREVIEW_ROWS]
    for record in shown:
        token = record.person_token
        if token in labels:
            record.person_label = labels[token]
        elif token in batch.person_tokens:
            record.person_label = f"new ({token.split('#')[0]})"
        else:
            record.person_label = token
    context = {
        "batch": batch,
        "records": shown,
        "more": max(0, len(records) - PREVIEW_ROWS),
        "summary": summary,
        "person_mapped": bool(batch.column_map.get("person")),
        "as_pending": batch.options.get("import_as") == "pending",
    }
    return render(request, "ledger/import_preview.html", context)


@require_POST
def import_commit(request, pk):
    batch = _staged(pk)
    try:
        stats = importer.commit_batch(
            batch,
            save_profile=request.POST.get("save_profile") == "on",
            profile_name=request.POST.get("profile_name", "").strip(),
        )
    except importer.BatchNotStaged:
        messages.warning(request, "This file was already imported.")
        return redirect("ledger:import_preview", pk=batch.pk)
    noun = "pending entries" if stats["import_as"] == "pending" else "transactions"
    note = f", {stats['removed']} earlier transactions removed as cancelled" if stats["removed"] else ""
    messages.success(request, f"Imported {stats['created']} {noun} ({stats['duplicates']} duplicates skipped{note}).")
    return redirect("ledger:import_preview", pk=batch.pk)


@require_POST
def import_cancel(request, pk):
    importer.cancel_batch(_staged(pk))
    messages.info(request, "Import cancelled. The staged data was deleted.")
    return redirect("ledger:imports")


@require_POST
def import_undo(request, pk):
    batch = get_object_or_404(ImportBatch, pk=pk, status=ImportBatch.Status.COMMITTED)
    transactions, pending = importer.undo_batch(batch)
    messages.success(request, f"Undid import: removed {transactions} transactions and {pending} pending entries.")
    return redirect("ledger:imports")


@require_POST
def profile_delete(request, pk):
    profile = get_object_or_404(ImportProfile, pk=pk)
    profile.delete()
    messages.success(request, f"Deleted saved mapping “{profile.name}”.")
    return redirect("ledger:imports")
