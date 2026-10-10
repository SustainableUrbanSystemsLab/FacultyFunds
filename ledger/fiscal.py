"""USG fiscal years run July 1 to June 30 and are named by the year they end: FY2027 is July 2026 to June 2027."""

import datetime as dt

FISCAL_YEAR_START_MONTH = 7


def fiscal_year(day: dt.date) -> int:
    return day.year + 1 if day.month >= FISCAL_YEAR_START_MONTH else day.year


def fiscal_year_bounds(year: int) -> tuple[dt.date, dt.date]:
    """First and last day of a fiscal year."""
    return dt.date(year - 1, FISCAL_YEAR_START_MONTH, 1), dt.date(year, FISCAL_YEAR_START_MONTH, 1) - dt.timedelta(days=1)
