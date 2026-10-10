"""Fringe and tuition rates by fiscal year.

USG fiscal years run July 1 to June 30 and are named by the year they end:
FY2027 is July 2026 to June 2027.
"""

from bisect import bisect_right
from collections import defaultdict
from decimal import Decimal

from ..fiscal import fiscal_year  # noqa: F401  (re-exported for callers of this module)
from ..models import FiscalYearRate

ZERO = Decimal("0")


class RateTable:
    """A person type's fringe rate or monthly tuition for any fiscal year.

    A fiscal year without its own value reuses the closest earlier year (before the first
    known year, the earliest one), so projections into unpublished years keep working.
    """

    METRICS = ("fringe_rate", "tuition_monthly")

    def __init__(self, rows=None):
        rows = FiscalYearRate.objects.all() if rows is None else rows
        self._years: dict[tuple[int, str], list[int]] = defaultdict(list)
        self._values: dict[tuple[int, str, int], Decimal] = {}
        for row in rows:
            for metric in self.METRICS:
                value = getattr(row, metric)
                if value is not None:
                    self._years[(row.person_type_id, metric)].append(row.fiscal_year)
                    self._values[(row.person_type_id, metric, row.fiscal_year)] = value
        for years in self._years.values():
            years.sort()

    def lookup(self, person_type_id, metric: str, year: int) -> tuple[Decimal, int | None]:
        """(value, fiscal year the value comes from), or (0, None) when nothing is set."""
        years = self._years.get((person_type_id, metric))
        if not years:
            return ZERO, None
        index = bisect_right(years, year) - 1
        source = years[index] if index >= 0 else years[0]
        return self._values[(person_type_id, metric, source)], source
