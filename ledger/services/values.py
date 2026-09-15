"""Parse amounts, dates, and month headers from spreadsheet text."""

import datetime as dt
import re
from decimal import Decimal, InvalidOperation

_AMOUNT_NOISE = re.compile(r"[,\s$€£]|usd", re.IGNORECASE)
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
_EXCEL_SERIAL = re.compile(r"^\d{5}(\.\d+)?$")

_DATE_FORMATS_US = [
    "%m/%d/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %I:%M %p",
    "%m/%d/%Y %I:%M:%S %p",
]
_DATE_FORMATS_ANY = [
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%d %b %Y",
    "%b %d, %Y",
    "%B %d, %Y",
    "%b %d %Y",
    "%B %d %Y",
]
_MONTH_FORMATS = [
    "%b %Y", "%B %Y", "%b-%Y", "%B-%Y", "%b-%y", "%B-%y", "%b %y", "%B %y",
    "%m/%Y", "%Y-%m", "%Y/%m", "%Y %b", "%Y %B",
]


def parse_amount(text: str) -> Decimal | None:
    """'$1,234.56' -> 1234.56; '(12.00)' and '12.00-' -> -12.00; blank or non-numeric -> None."""
    s = text.strip()
    if not s:
        return None
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    s = _AMOUNT_NOISE.sub("", s)
    if s.endswith("-") and len(s) > 1:
        negative, s = not negative, s[:-1]
    try:
        value = Decimal(s)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return -value if negative else value


def _plausible(date: dt.date) -> dt.date | None:
    return date if 1990 <= date.year <= 2100 else None


def parse_date(text: str, day_first: bool = False) -> dt.date | None:
    s = " ".join(text.strip().split())
    if not s:
        return None
    if _ISO_PREFIX.match(s):
        try:
            return _plausible(dt.date.fromisoformat(s[:10]))
        except ValueError:
            return None
    formats = _DATE_FORMATS_US
    if day_first:
        formats = [fmt.replace("%m/%d", "%d/%m").replace("%m-%d", "%d-%m") for fmt in formats]
    for fmt in formats + _DATE_FORMATS_ANY:
        try:
            return _plausible(dt.datetime.strptime(s, fmt).date())
        except ValueError:
            continue
    if _EXCEL_SERIAL.match(s):
        serial = float(s)
        if 30000 < serial < 80000:
            return dt.date(1899, 12, 30) + dt.timedelta(days=int(serial))
    return None


def parse_month_header(text: str) -> dt.date | None:
    """Column headers in wide worksheets: 'Jul 2026', 'July-26', '07/2026', '2026-07' -> first of month."""
    s = " ".join(text.strip().split())
    if not s:
        return None
    for fmt in _MONTH_FORMATS:
        try:
            parsed = dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
        return _plausible(parsed.replace(day=1))
    return parse_date(s)


def looks_like_value(text: str) -> bool:
    """True for cells that are numbers or dates (i.e. probably data, not a header)."""
    return parse_amount(text) is not None or parse_date(text) is not None
