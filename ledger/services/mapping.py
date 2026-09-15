"""Find the header row and suggest which column feeds which field."""

import hashlib
import re
from dataclasses import dataclass

from .pii import normalize_header
from .values import looks_like_value


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    help: str
    synonyms: tuple[str, ...]
    required: bool = False
    multi: bool = False
    avoid: tuple[str, ...] = ()
    payroll_only: bool = False


FIELDS = (
    FieldSpec(
        "date", "Date", "Expense, transaction, or pay period end date.",
        ("date", "expense date", "expense report date", "transaction date", "accounting date", "posting date",
         "journal date", "expended date", "pay end date", "pay end dt", "pay period end date", "check date",
         "payment date", "period end date", "budget date", "effective date", "report date", "submitted date"),
        required=True, avoid=("created", "updated", "birth", "hire"),
    ),
    FieldSpec(
        "amount", "Amount", "Select several month columns to import a wide worksheet (one column per month).",
        ("amount", "total amount", "expense amount", "line amount", "transaction amount", "monetary amount",
         "ledger amount", "net amount", "extended amount", "enc amt", "encumbrance amount", "expended amount",
         "earnings amount", "gross amount", "payroll amount", "salary amount", "distribution amount",
         "charge amount", "total", "actuals", "actual amount"),
        required=True, multi=True, avoid=("currency", "quantity", "rate", "per unit", "count", "percent", "fringe"),
    ),
    FieldSpec(
        "description", "Description", "",
        ("memo", "description", "business purpose", "line memo", "expense item", "item description", "purpose",
         "descr", "comment", "comments", "justification", "transaction description", "journal line memo",
         "line description"),
    ),
    FieldSpec(
        "reference", "Reference / document number", "",
        ("expense report", "expense report number", "report number", "document number", "journal",
         "journal number", "journal id", "transaction id", "reference", "reference number", "pay run id",
         "report id", "check number", "voucher", "invoice number", "po number", "requisition"),
        avoid=("date", "status", "paid", "amount"),
    ),
    FieldSpec(
        "category_raw", "Spend category / account", "Matched against category rules.",
        ("spend category", "ledger account", "expense item", "account", "acct", "earnings code", "earn code",
         "earnings type", "object class", "budget category", "category", "account description",
         "transaction typ", "transaction type", "pay type", "cost element"),
        avoid=("bank", "date"),
    ),
    FieldSpec(
        "worktags", "Worktag / fund columns", "Every column that may contain a worktag or fund ID.",
        ("worktags", "additional worktags", "worktag", "cost center", "grant", "gift", "project", "program",
         "fund", "award", "designated", "combo code", "chartfield1", "chartfield", "dept id", "project id",
         "driver worktag", "funding source", "activity"),
        multi=True, avoid=("date", "amount", "status", "type", "manager", "name"),
    ),
    FieldSpec(
        "person", "Person (name or employee ID)", "Pseudonymized on upload; links rows to people.",
        ("name", "employee name", "employee", "worker", "pay to", "payee", "traveler", "emp id", "emplid",
         "empl id", "employee id", "person"),
        avoid=("type", "department", "company", "cost center", "grant", "project"),
    ),
    FieldSpec(
        "status", "Status", "Rows whose status contains a skip word are ignored.",
        ("status", "expense report status", "approval status", "document status", "line status"),
    ),
    FieldSpec(
        "fringe", "Fringe amount", "Payroll: employer benefit cost for the row.",
        ("fringe", "fringe amount", "fringe benefits", "benefits", "benefit amount", "employer contribution",
         "employer contributions", "ere", "employer paid benefits", "fringe cost"),
        payroll_only=True,
    ),
)
FIELD_BY_KEY = {spec.key: spec for spec in FIELDS}

EXACT = 100
CONTAINS = 50


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _score(header_norm: str, spec: FieldSpec) -> tuple[int, int]:
    """(score, -synonym index); a score of 0 means no match."""
    if not header_norm or any(word in header_norm for word in spec.avoid):
        return (0, 0)
    best = (0, 0)
    for index, synonym in enumerate(spec.synonyms):
        if header_norm == synonym:
            candidate = (EXACT, -index)
        elif re.search(rf"(?<![a-z0-9]){re.escape(synonym)}(?![a-z0-9])", header_norm):
            candidate = (CONTAINS + min(len(synonym), 40), -index)
        else:
            continue
        best = max(best, candidate)
    return best


def detect_header_row(rows: list[list[str]], scan: int = 40) -> int:
    """Index of the most header-like row: mostly text, known column names, data-width rows below."""
    best_index, best_score = 0, float("-inf")
    for index, row in enumerate(rows[:scan]):
        cells = [cell for cell in row if cell.strip()]
        if len(cells) < 2:
            continue
        text_cells = [cell for cell in cells if not looks_like_value(cell)]
        if len(text_cells) < 0.6 * len(cells):
            continue
        hits = sum(1 for cell in text_cells if any(_score(normalize_header(cell), spec)[0] for spec in FIELDS))
        following = [r for r in rows[index + 1 : index + 6] if any(x.strip() for x in r)]
        wide_rows = sum(1 for r in following if sum(1 for x in r if x.strip()) >= max(2, len(cells) // 2))
        score = hits * 3 + len(text_cells) * 0.5 + wide_rows
        if score > best_score:
            best_index, best_score = index, score
    return best_index


def make_headers(rows: list[list[str]], header_index: int) -> list[str]:
    """Unique, non-empty header names for every column in use."""
    header_row = rows[header_index] if header_index < len(rows) else []
    width = max((len(row) for row in rows[header_index:]), default=0)
    headers, seen = [], {}
    for index in range(width):
        raw = header_row[index] if index < len(header_row) else ""
        name = " ".join(raw.split()) or f"Column {column_letter(index)}"
        count = seen.get(name.lower(), 0) + 1
        seen[name.lower()] = count
        headers.append(name if count == 1 else f"{name} ({count})")
    return headers


def header_signature(headers: list[str], source: str) -> str:
    text = source + "|" + "|".join(normalize_header(header) for header in headers)
    return hashlib.sha256(text.encode()).hexdigest()


def auto_map(headers: list[str], source: str) -> dict:
    normalized = [normalize_header(header) for header in headers]
    candidates = []
    for spec in FIELDS:
        if spec.payroll_only and source == "workday_expense":
            continue
        for column, header in enumerate(normalized):
            score = _score(header, spec)
            if score[0]:
                candidates.append((score, spec.key, column))
    candidates.sort(key=lambda item: (item[0], -item[2]), reverse=True)

    mapping: dict = {}
    used: set[int] = set()
    for _, key, column in candidates:
        if key == "worktags" or key in mapping or column in used:
            continue
        mapping[key] = [headers[column]] if FIELD_BY_KEY[key].multi else headers[column]
        used.add(column)
    worktag_columns = sorted({column for _, key, column in candidates if key == "worktags" and column not in used})
    mapping["worktags"] = [headers[column] for column in worktag_columns]
    return mapping


def column_indexes(column_map: dict, headers: list[str]) -> dict:
    """Header names -> column positions. Multi fields give lists, single fields an int or None."""
    position = {header: index for index, header in enumerate(headers)}
    out = {}
    for spec in FIELDS:
        value = column_map.get(spec.key)
        if spec.multi:
            values = value if isinstance(value, list) else ([value] if value else [])
            out[spec.key] = [position[v] for v in values if v in position]
        else:
            out[spec.key] = position.get(value) if isinstance(value, str) else None
    return out
