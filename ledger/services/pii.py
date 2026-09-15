"""Pseudonymize people in uploaded rows before anything is saved.

Columns that look like person names or employee IDs are replaced by tokens such as
"JD#4f09a1c2b3" (initials + short keyed hash). The full keyed hash (HMAC-SHA256 with
settings.PII_HMAC_KEY) is what links rows to a Person; the real value is never stored.
Names found in person columns are also masked in other cells of the same row, and
SSN-like numbers are redacted everywhere.
"""

import hashlib
import hmac
import re
import unicodedata

from django.conf import settings

NAME_HEADERS = {
    "name", "employee name", "employee", "worker", "payee", "pay to", "traveler", "traveller",
    "person", "full name", "emp name", "empl name", "employee full name", "worker name", "person name",
    "submitted by", "created by", "requester", "requestor", "preparer", "expense report owner",
    "cardholder", "card holder", "last name", "first name", "preferred name", "legal name",
    "on behalf of", "name last first",
}
NAME_HINTS = ("employee name", "worker name", "person name", "full name", "last name", "first name",
              "preferred name", "legal name", "cardholder")
ID_HEADERS = {
    "emplid", "empl id", "emp id", "employee id", "worker id", "person id", "gtid", "gt id", "student id",
    "id", "employee number", "empl no", "ssn", "social security number", "national id", "badge id",
}
NOT_PERSON_WORDS = ("cost center", "grant", "gift", "project", "program", "fund", "supplier", "vendor",
                    "category", "account", "department", "dept", "company", "report", "item", "sponsor",
                    "award", "position", "job", "earn", "combo", "budget", "file", "sheet", "type", "run")

TOKEN_PATTERN = re.compile(r"^(?:[A-Z]{1,2}|ID)#[0-9a-f]{10}$")
_ID_IN_NAME = re.compile(r"\(\s*(\d{4,})\s*\)")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md", "dr", "mr", "ms", "mrs", "prof"}


def normalize_header(header: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", header.lower()).split())


def classify_header(header: str) -> str | None:
    """'name' / 'id' for person columns, else None."""
    h = normalize_header(header)
    if not h:
        return None
    if h in ID_HEADERS:
        return "id"
    if h in NAME_HEADERS:
        return "name"
    if any(word in h for word in NOT_PERSON_WORDS):
        return None
    if any(hint in h for hint in NAME_HINTS):
        return "name"
    if re.search(r"\b(empl ?id|employee id|worker id)\b", h):
        return "id"
    return None


def keyed_hash(kind: str, normalized: str) -> str:
    return hmac.new(settings.PII_HMAC_KEY.encode(), f"{kind}:{normalized}".encode(), hashlib.sha256).hexdigest()


def _ascii_words(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.sub(r"[^A-Za-z' -]+", " ", text).replace("'", "").split()
    return [w for w in words if w.lower().strip("-") not in _SUFFIXES]


def split_name(value: str) -> tuple[str, str]:
    """('jane', 'doe') from 'Doe, Jane A.', 'Jane A. Doe', or 'Jane Doe (000123456)'."""
    text = _ID_IN_NAME.sub(" ", value)
    if "," in text:
        last_part, _, first_part = text.partition(",")
        first_words, last_words = _ascii_words(first_part), _ascii_words(last_part)
        first = first_words[0] if first_words else ""
        last = " ".join(last_words)
    else:
        words = _ascii_words(text)
        first = words[0] if words else ""
        last = words[-1] if len(words) > 1 else ""
    return first.lower(), last.lower()


def pseudonymize(value: str, kind: str) -> tuple[str, dict] | None:
    text = value.strip()
    if not text:
        return None
    if kind == "id":
        digits = re.sub(r"\D", "", text)
        normalized = (digits.lstrip("0") or "0") if digits else text.lower()
        key = keyed_hash("id", normalized)
        return f"ID#{key[:10]}", {"key": key, "kind": "id", "initials": ""}

    first, last = split_name(text)
    if not first and not last:
        return None
    key = keyed_hash("name", f"{first} {last}".strip())
    initials = (first[:1] + last[:1]).upper()
    info = {"key": key, "kind": "name", "initials": initials}
    if match := _ID_IN_NAME.search(text):
        info["id_key"] = keyed_hash("id", match.group(1).lstrip("0") or "0")
    return f"{initials or 'P'}#{key[:10]}", info


def _name_pattern(value: str) -> re.Pattern | None:
    first, last = split_name(value)
    if len(first) < 2 or len(last) < 2:
        return None
    f = r"\s+".join(map(re.escape, first.split()))
    l = r"\s+".join(map(re.escape, last.split()))
    middle = r"(?:\s+[A-Za-z]\.?)?"
    return re.compile(rf"\b{f}{middle}\s+{l}\b|\b{l}\s*,\s*{f}{middle}\b", re.IGNORECASE)


def scrub_rows(rows: list[list[str]], header_index: int) -> tuple[list[list[str]], dict, list[int]]:
    """Pseudonymize person columns below the header row.

    Returns (rows, tokens, person column indexes). `tokens` maps token -> key info.
    """
    headers = rows[header_index] if header_index < len(rows) else []
    person_cols = {i: kind for i, header in enumerate(headers) if (kind := classify_header(header))}
    tokens: dict[str, dict] = {}
    out = [list(row) for row in rows]

    for row in out[header_index + 1 :]:
        masks: list[tuple[re.Pattern, str]] = []
        for col, kind in person_cols.items():
            if col >= len(row) or not row[col].strip():
                continue
            original = row[col]
            if TOKEN_PATTERN.match(original):  # already pseudonymized (header row changed)
                continue
            result = pseudonymize(original, kind)
            if result is None:
                continue
            token, info = result
            tokens[token] = info
            row[col] = token
            if kind == "name" and (pattern := _name_pattern(original)):
                masks.append((pattern, info["initials"] or "[person]"))
            digits = re.sub(r"\D", "", original)
            if len(digits) >= 5:
                masks.append((re.compile(rf"(?<!\d){re.escape(digits)}(?!\d)"), "[id]"))
        for col, cell in enumerate(row):
            if col in person_cols or not cell:
                continue
            cleaned = _SSN.sub("[redacted]", cell)
            for pattern, replacement in masks:
                cleaned = pattern.sub(replacement, cleaned)
            row[col] = cleaned
    return out, tokens, sorted(person_cols)
