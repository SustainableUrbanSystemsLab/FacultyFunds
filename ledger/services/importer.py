"""Import pipeline: upload -> stage (pseudonymized) -> map columns -> preview -> commit. Undo removes a batch."""

import datetime as dt
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal

from django.conf import settings
from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from ..models import (
    Category,
    CategoryRule,
    ImportBatch,
    ImportProfile,
    PendingEntry,
    Person,
    PersonKey,
    Source,
    Transaction,
    Worktag,
)
from . import fileparse, mapping, pii
from .rules import CategoryMatcher, WorktagMatcher, describe_code
from .values import parse_amount, parse_date, parse_month_header

DEFAULT_SKIP_STATUSES = "draft, cancel, canceled, cancelled, denied, sent back"


def default_options(source: str) -> dict:
    return {
        "import_as": "actual",
        "negate": False,
        "skip_statuses": DEFAULT_SKIP_STATUSES,
        "skip_zero": True,
        "day_first": False,
        "default_category": Category.SALARIES if source == Source.ONEUSG_PAYROLL else "",
        "default_worktag": None,
    }


def batch_headers(batch: ImportBatch) -> list[str]:
    return mapping.make_headers(batch.rows, batch.header_row)


# --- Staging ------------------------------------------------------------------


def stage_upload(filename: str, data: bytes, source: str) -> ImportBatch:
    purge_stale_batches()
    sheets = fileparse.read_tables(filename, data)
    staged, tokens = {}, {}
    for name, rows in sheets.items():
        scrubbed, sheet_tokens, _ = pii.scrub_rows(rows, mapping.detect_header_row(rows))
        staged[name] = scrubbed
        tokens.update(sheet_tokens)
    batch = ImportBatch(
        source=source,
        filename=filename[:255],
        sheets=staged,
        person_tokens=tokens,
        options=default_options(source),
    )
    configure_sheet(batch, max(staged, key=lambda name: len(staged[name])))
    batch.save()
    return batch


def configure_sheet(batch: ImportBatch, sheet_name: str, header_row: int | None = None) -> None:
    """Select a sheet/header row, then apply a saved profile for these headers or auto-map."""
    batch.sheet_name = sheet_name
    rows = batch.rows
    if header_row is None:
        batch.header_row = mapping.detect_header_row(rows)
    else:
        batch.header_row = max(0, min(header_row, len(rows) - 1))
        scrubbed, tokens, _ = pii.scrub_rows(rows, batch.header_row)
        batch.sheets[sheet_name] = scrubbed
        batch.person_tokens.update(tokens)

    headers = batch_headers(batch)
    signature = mapping.header_signature(headers, batch.source)
    profile = ImportProfile.objects.filter(source=batch.source, header_signature=signature).first()
    if profile:
        batch.profile = profile
        batch.column_map = profile.column_map
        batch.options = {**default_options(batch.source), **profile.options}
    else:
        batch.profile = None
        batch.column_map = mapping.auto_map(headers, batch.source)


def person_columns(batch: ImportBatch) -> list[str]:
    headers = batch_headers(batch)
    header_row = batch.rows[batch.header_row] if batch.rows else []
    return [headers[i] for i, raw in enumerate(header_row) if i < len(headers) and pii.classify_header(raw)]


# --- Parsing ------------------------------------------------------------------


@dataclass
class ParsedRow:
    line: int
    date: dt.date | None
    amount: Decimal | None
    description: str = ""
    reference: str = ""
    raw_category: str = ""
    raw_worktags: str = ""
    raw_status: str = ""
    person_token: str = ""
    column_label: str = ""
    kind: str = "amount"
    worktag: Worktag | None = None
    worktag_note: str = ""
    category: Category | None = None
    skip_reason: str = ""
    dedupe_key: str = ""
    duplicate: bool = False

    @property
    def importable(self) -> bool:
        return not self.skip_reason and not self.duplicate


def parse_batch(batch: ImportBatch) -> tuple[list[ParsedRow], dict]:
    headers = batch_headers(batch)
    cols = mapping.column_indexes(batch.column_map, headers)
    options = {**default_options(batch.source), **batch.options}
    categories = {category.code: category for category in Category.objects.all()}
    category_matcher = CategoryMatcher(CategoryRule.objects.select_related("category"))
    worktags = list(Worktag.objects.all())
    worktag_matcher = WorktagMatcher(worktags)
    default_worktag = next((w for w in worktags if w.pk == options.get("default_worktag")), None)
    default_category = categories.get(options.get("default_category") or "")
    skip_words = [word.strip().lower() for word in options.get("skip_statuses", "").split(",") if word.strip()]

    amount_cols = cols["amount"]
    wide = len(amount_cols) > 1
    month_dates = {c: parse_month_header(headers[c]) for c in amount_cols} if wide else {}
    pieces = [("amount", c, headers[c] if wide else "") for c in amount_cols]
    if cols["fringe"] is not None:
        pieces.append(("fringe", cols["fringe"], ""))

    def cell(row, index):
        return row[index].strip() if index is not None and index < len(row) else ""

    records: list[ParsedRow] = []
    unknown_codes: dict[str, tuple[str, str]] = {}
    for line, row in enumerate(batch.rows[batch.header_row + 1 :], start=batch.header_row + 2):
        if not any(value.strip() for value in row):
            continue
        worktag_text = "\n".join(value for i in cols["worktags"] if (value := cell(row, i)))
        base = {
            "description": cell(row, cols["description"])[:500],
            "reference": cell(row, cols["reference"])[:120],
            "raw_category": cell(row, cols["category_raw"])[:200],
            "raw_worktags": worktag_text[:2000],
            "raw_status": cell(row, cols["status"])[:60],
            "person_token": cell(row, cols["person"])[:80],
        }
        row_date = parse_date(cell(row, cols["date"]), options["day_first"]) if cols["date"] is not None else None
        worktag, note = worktag_matcher.match(worktag_text)
        if worktag is None and default_worktag is not None:
            worktag, note = default_worktag, ""
        for code in worktag_matcher.unknown_codes(worktag_text):
            unknown_codes.setdefault(code, describe_code(code, worktag_text))
        matched_category = category_matcher.match(base["raw_category"], base["description"])
        status = base["raw_status"].lower()
        status_skip = next((word for word in skip_words if word in status), None)

        for kind, column, label in pieces:
            text = cell(row, column)
            if not text and (wide or kind == "fringe"):
                continue
            amount = parse_amount(text)
            if amount is not None and options["negate"]:
                amount = -amount
            record = ParsedRow(
                line=line,
                date=(month_dates.get(column) or row_date) if wide else row_date,
                amount=amount,
                column_label=label,
                kind=kind,
                worktag=worktag,
                worktag_note=note,
                **base,
            )
            record.category = categories.get(Category.FRINGE) if kind == "fringe" else (matched_category or default_category)
            if status_skip:
                record.skip_reason = f"status: {base['raw_status']}"
            elif amount is None:
                record.skip_reason = "no amount" if not text else f"unreadable amount: {text[:24]}"
            elif amount == 0 and options["skip_zero"]:
                record.skip_reason = "zero amount"
            elif record.date is None:
                record.skip_reason = "no readable date"
            records.append(record)

    _assign_dedupe_keys(batch, records, options)
    return records, _summarize(batch, records, unknown_codes)


def _assign_dedupe_keys(batch: ImportBatch, records: list[ParsedRow], options: dict) -> None:
    occurrences: Counter = Counter()
    for record in records:
        if record.skip_reason:
            continue
        person = batch.person_tokens.get(record.person_token, {}).get("key", record.person_token)
        signature = [
            batch.source, options["import_as"], record.date.isoformat(), str(record.amount.quantize(Decimal("0.01"))),
            record.reference, record.description, record.raw_category, record.raw_worktags, person,
            record.kind, record.column_label,
        ]
        text = json.dumps(signature)
        occurrences[text] += 1
        record.dedupe_key = hashlib.sha256(f"{text}#{occurrences[text]}".encode()).hexdigest()

    model = PendingEntry if options["import_as"] == "pending" else Transaction
    keys = [record.dedupe_key for record in records if record.dedupe_key]
    existing = set()
    for start in range(0, len(keys), 500):
        existing.update(model.objects.filter(dedupe_key__in=keys[start : start + 500]).values_list("dedupe_key", flat=True))
    for record in records:
        record.duplicate = record.dedupe_key in existing


def _summarize(batch: ImportBatch, records: list[ParsedRow], unknown_codes: dict) -> dict:
    importable = [r for r in records if r.importable]
    tokens = {r.person_token for r in importable if r.person_token in batch.person_tokens}
    key_list = [batch.person_tokens[token]["key"] for token in tokens]
    known = set(PersonKey.objects.filter(key_hash__in=key_list).values_list("key_hash", flat=True))
    by_fund = Counter()
    for record in importable:
        by_fund[record.worktag.code if record.worktag else ""] += record.amount
    return {
        "rows": len(records),
        "importable": len(importable),
        "duplicates": sum(1 for r in records if r.duplicate),
        "skipped": dict(Counter(r.skip_reason.split(":")[0] for r in records if r.skip_reason)),
        "no_fund": sum(1 for r in importable if r.worktag is None),
        "no_category": sum(1 for r in importable if r.category is None),
        "total": sum((r.amount for r in importable), Decimal("0")),
        "by_fund": sorted(by_fund.items()),
        "new_people": sum(1 for token in tokens if batch.person_tokens[token]["key"] not in known),
        "unknown_codes": sorted(unknown_codes.items()),
    }


def people_labels(batch: ImportBatch) -> dict[str, str]:
    """Token -> existing alias, for showing who rows belong to in the preview."""
    keys = {info["key"]: token for token, info in batch.person_tokens.items()}
    labels = {}
    for person_key in PersonKey.objects.filter(key_hash__in=list(keys)).select_related("person"):
        labels[keys[person_key.key_hash]] = person_key.person.alias
    return labels


# --- Commit / undo ------------------------------------------------------------


def _unique_alias(base: str) -> str:
    base = (base or "Person")[:70]
    alias, n = base, 1
    while Person.objects.filter(alias=alias).exists():
        n += 1
        alias = f"{base}-{n}"
    return alias


def resolve_people(batch: ImportBatch, tokens: set[str]) -> dict[str, Person]:
    people = {}
    for token in tokens:
        info = batch.person_tokens.get(token)
        if not info:
            continue
        keys = [(info["key"], info["kind"])]
        if info.get("id_key"):
            keys.append((info["id_key"], PersonKey.Kind.EMPLOYEE_ID))
        existing = PersonKey.objects.filter(key_hash__in=[k for k, _ in keys]).select_related("person").first()
        person = existing.person if existing else Person.objects.create(alias=_unique_alias(info.get("initials")))
        for key, kind in keys:
            PersonKey.objects.get_or_create(key_hash=key, defaults={"person": person, "kind": kind})
        people[token] = person
    return people


@db_transaction.atomic
def commit_batch(batch: ImportBatch, save_profile: bool = False, profile_name: str = "") -> dict:
    records, summary = parse_batch(batch)
    importable = [r for r in records if r.importable]
    people = resolve_people(batch, {r.person_token for r in importable if r.person_token})
    options = {**default_options(batch.source), **batch.options}
    fallback_category = Category.objects.filter(code=Category.OTHER).first()

    skipped_no_fund = 0
    if options["import_as"] == "pending":
        objects = []
        for r in importable:
            if r.worktag is None:
                skipped_no_fund += 1
                continue
            objects.append(
                PendingEntry(
                    worktag=r.worktag, category=r.category or fallback_category, person=people.get(r.person_token),
                    description=(r.description or r.raw_category or r.reference or "Imported item")[:300],
                    amount=r.amount, expected_date=r.date, batch=batch, dedupe_key=r.dedupe_key,
                )
            )
        PendingEntry.objects.bulk_create(objects, batch_size=500)
    else:
        objects = [
            Transaction(
                worktag=r.worktag, category=r.category, person=people.get(r.person_token), date=r.date,
                amount=r.amount, description=r.description, reference=r.reference, source=batch.source,
                raw_category=r.raw_category, raw_worktags=r.raw_worktags, raw_status=r.raw_status,
                batch=batch, dedupe_key=r.dedupe_key,
            )
            for r in importable
        ]
        Transaction.objects.bulk_create(objects, batch_size=500)

    now = timezone.now()
    headers = batch_headers(batch)
    if save_profile:
        signature = mapping.header_signature(headers, batch.source)
        profile, _ = ImportProfile.objects.update_or_create(
            source=batch.source,
            header_signature=signature,
            defaults={
                "name": (profile_name or batch.filename)[:120],
                "column_map": batch.column_map,
                "options": options,
                "last_used_at": now,
            },
        )
        batch.profile = profile
    elif batch.profile:
        ImportProfile.objects.filter(pk=batch.profile.pk).update(last_used_at=now)

    batch.stats = {
        "created": len(objects),
        "import_as": options["import_as"],
        "duplicates": summary["duplicates"],
        "skipped": summary["skipped"],
        "skipped_no_fund": skipped_no_fund,
        "no_fund": summary["no_fund"] if options["import_as"] == "actual" else 0,
        "no_category": summary["no_category"],
        "new_people": summary["new_people"],
        "total": str(sum((o.amount for o in objects), Decimal("0"))),
        "unknown_codes": summary["unknown_codes"],
    }
    batch.status = ImportBatch.Status.COMMITTED
    batch.committed_at = now
    batch.sheets = {}
    batch.person_tokens = {}
    batch.save()
    return batch.stats


@db_transaction.atomic
def undo_batch(batch: ImportBatch) -> tuple[int, int]:
    PendingEntry.objects.filter(reconciled_with__batch=batch).update(
        status=PendingEntry.Status.PENDING, reconciled_with=None
    )
    transactions = batch.transactions.count()
    pending = batch.pending_entries.count()
    batch.transactions.all().delete()
    batch.pending_entries.all().delete()
    batch.status = ImportBatch.Status.UNDONE
    batch.save(update_fields=["status"])
    return transactions, pending


def cancel_batch(batch: ImportBatch) -> None:
    batch.status = ImportBatch.Status.CANCELLED
    batch.sheets = {}
    batch.person_tokens = {}
    batch.save(update_fields=["status", "sheets", "person_tokens"])


def rematch_transactions() -> tuple[int, int]:
    """Re-run fund and category matching for imported transactions that are still unassigned."""
    worktag_matcher = WorktagMatcher(list(Worktag.objects.all()))
    category_matcher = CategoryMatcher(CategoryRule.objects.select_related("category"))
    funds = categories = 0
    pending_review = Transaction.objects.filter(Q(worktag__isnull=True) | Q(category__isnull=True)).exclude(
        source=Source.MANUAL
    )
    for txn in pending_review.iterator():
        changed = []
        if txn.worktag_id is None:
            worktag, _ = worktag_matcher.match(txn.raw_worktags)
            if worktag:
                txn.worktag = worktag
                changed.append("worktag")
                funds += 1
        if txn.category_id is None:
            category = category_matcher.match(txn.raw_category, txn.description)
            if category:
                txn.category = category
                changed.append("category")
                categories += 1
        if changed:
            txn.save(update_fields=changed)
    return funds, categories


def purge_stale_batches() -> int:
    cutoff = timezone.now() - dt.timedelta(hours=settings.STAGED_IMPORT_TTL_HOURS)
    stale = list(ImportBatch.objects.filter(status=ImportBatch.Status.STAGED, created_at__lt=cutoff))
    for batch in stale:
        cancel_batch(batch)
    return len(stale)
