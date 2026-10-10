"""Budget vs. actuals, planned payroll, F&A, burn rate, runway, and alerts for each fund.

Rules (kept deliberately explainable):
* Actuals = imported/manual transactions. Uncategorized amounts count as "Other".
* Payroll salary with no fringe line for the same person and month gets estimated fringe.
* Fringe and tuition use the person type's rate for the fiscal year (July–June) each month
  falls in; a year without rates reuses the closest earlier year and raises an alert.
* Planned payroll (appointments) counts for every month that has no salary actual for that
  person on that fund; planned tuition likewise for months without a tuition actual.
* F&A = fund rate x F&A-eligible spending (MTDC categories), unless the fund uses imported
  F&A lines for actuals.
* Burn rate = average monthly actual spending over the last 6 complete months.
"""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.utils import timezone

from ..models import ZERO, Category, PendingEntry, Person, Transaction, Worktag
from .rates import RateTable, fiscal_year

BURN_WINDOW_MONTHS = 6
MAX_RUNWAY_MONTHS = 1200
DAYS_PER_MONTH = Decimal("30.44")
CENT = Decimal("0.01")


def month_start(day: dt.date) -> dt.date:
    return day.replace(day=1)


def add_months(day: dt.date, months: int) -> dt.date:
    years, month_index = divmod(day.month - 1 + months, 12)
    return dt.date(day.year + years, month_index + 1, 1)


def month_end(day: dt.date) -> dt.date:
    return add_months(day, 1) - dt.timedelta(days=1)


def iter_months(start: dt.date, end: dt.date):
    month = month_start(start)
    while month <= end:
        yield month
        month = add_months(month, 1)


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass
class Line:
    category: Category | None
    budget: Decimal = ZERO
    actual: Decimal = ZERO
    pending: Decimal = ZERO
    planned: Decimal = ZERO
    estimated: Decimal = ZERO  # portion of `actual` that is calculated, not reported

    @property
    def committed(self) -> Decimal:
        return self.pending + self.planned

    @property
    def projected(self) -> Decimal:
        return self.actual + self.pending + self.planned

    @property
    def remaining(self) -> Decimal:
        return self.budget - self.actual - self.pending

    @property
    def projected_remaining(self) -> Decimal:
        return self.budget - self.projected

    @property
    def pct_spent(self) -> Decimal | None:
        return self.actual / self.budget * 100 if self.budget else None

    def round(self) -> None:
        for name in ("budget", "actual", "pending", "planned", "estimated"):
            setattr(self, name, cents(getattr(self, name)))


@dataclass
class Alert:
    level: str  # danger | warn | info
    message: str
    worktag: Worktag | None = None


@dataclass
class FundSummary:
    worktag: Worktag
    lines: list[Line]
    total: Line
    burn_rate: Decimal | None
    months_left: Decimal | None
    runway_months: Decimal | None
    run_out_date: dt.date | None
    burn_projected_end: Decimal | None
    plan_rows: list[dict]
    monthly_actual: dict[dt.date, Decimal]
    monthly_planned: dict[dt.date, Decimal]
    burn_months: int = 0  # how many complete months the burn rate averages over
    alerts: list[Alert] = field(default_factory=list)

    @property
    def days_left(self) -> int | None:
        end = self.worktag.end_date
        return (end - timezone.localdate()).days if end else None


def summarize_fund(
    worktag: Worktag,
    today: dt.date | None = None,
    categories: list[Category] | None = None,
    rates: RateTable | None = None,
) -> FundSummary:
    today = today or timezone.localdate()
    categories = categories if categories is not None else list(Category.objects.all())
    rates = rates if rates is not None else RateTable()
    by_code = {c.code: c for c in categories}
    code_of = {c.pk: c.code for c in categories}
    lines = {c.pk: Line(c) for c in categories}
    fa_rate = worktag.fa_rate / 100
    calculate_fa = worktag.fa_actuals == Worktag.FAActuals.CALCULATE
    eligible_ids = {c.pk for c in categories if c.fa_eligible and c.code != Category.FA}

    def line_for(code: str) -> Line:
        return lines[by_code[code].pk]

    for budget_line in worktag.budget_lines.all():
        lines[budget_line.category_id].budget += budget_line.amount

    # --- Actuals
    monthly_actual: dict[dt.date, Decimal] = defaultdict(Decimal)
    monthly_eligible: dict[dt.date, Decimal] = defaultdict(Decimal)
    salary = defaultdict(Decimal)  # (person_id, month) -> amount
    covered = {Category.SALARIES: set(), Category.FRINGE: set(), Category.TUITION: set()}
    uncategorized = ignored_fa = ZERO
    other = line_for(Category.OTHER)
    rows = Transaction.objects.filter(worktag=worktag).values_list("category_id", "person_id", "date", "amount")
    for category_id, person_id, date, amount in rows:
        month = month_start(date)
        code = code_of.get(category_id)
        if code == Category.FA and calculate_fa:
            ignored_fa += amount
            continue
        target = lines[category_id] if category_id else other
        if category_id is None:
            uncategorized += amount
        target.actual += amount
        monthly_actual[month] += amount
        if target.category.pk in eligible_ids:
            monthly_eligible[month] += amount
        if code in covered:
            covered[code].add((person_id, month))
        if code == Category.SALARIES:
            salary[(person_id, month)] += amount

    appointments = list(worktag.appointments.select_related("person__person_type"))
    people_without_type = set()

    def is_covered(code: str, person_id, month) -> bool:
        return (person_id, month) in covered[code] or (None, month) in covered[code]

    # Rates follow the fiscal year of each month. Remember years that had to reuse an earlier year.
    carried: dict[tuple[str, int], int | None] = {}  # (person type, fiscal year) -> year used, None if no rates

    def rate_for(person_type, metric: str, month: dt.date) -> Decimal:
        if person_type is None:
            return ZERO
        year = fiscal_year(month)
        value, source = rates.lookup(person_type.pk, metric, year)
        if source is None and metric == "fringe_rate":
            carried.setdefault((person_type.name, year), None)
        elif source is not None and source != year and value:
            carried.setdefault((person_type.name, year), source)
        return value

    # Estimated fringe for payroll months that have salary but no fringe line.
    fringe_line = line_for(Category.FRINGE)
    unestimated_payroll = False
    if salary:
        people = Person.objects.select_related("person_type").in_bulk({p for p, _ in salary if p})
        for (person_id, month), amount in salary.items():
            if is_covered(Category.FRINGE, person_id, month):
                continue
            person = people.get(person_id)
            if person is None:
                unestimated_payroll = True
                continue
            override = next(
                (
                    a.fringe_rate_override
                    for a in appointments
                    if a.person_id == person_id
                    and a.fringe_rate_override is not None
                    and a.start_date <= month_end(month)
                    and a.end_date >= month
                ),
                None,
            )
            if override is None and person.person_type is None:
                people_without_type.add(person.alias)
                continue
            rate = override if override is not None else rate_for(person.person_type, "fringe_rate", month)
            estimate = amount * rate / 100
            fringe_line.actual += estimate
            fringe_line.estimated += estimate
            monthly_actual[month] += estimate
            if fringe_line.category.pk in eligible_ids:
                monthly_eligible[month] += estimate

    # --- Planned payroll and tuition
    current = month_start(today)
    monthly_planned: dict[dt.date, Decimal] = defaultdict(Decimal)
    monthly_planned_eligible: dict[dt.date, Decimal] = defaultdict(Decimal)
    salary_line, tuition_line = line_for(Category.SALARIES), line_for(Category.TUITION)
    plan_rows = []
    past_planned_months: set[dt.date] = set()  # months already over with planned pay but no payroll actual
    unlinked_salary_months = sorted({month for person_id, month in covered[Category.SALARIES] if person_id is None})
    for appointment in appointments:
        start = max(appointment.start_date, worktag.tracking_start or appointment.start_date)
        end = appointment.end_date
        person_type = appointment.person.person_type
        if person_type is None and appointment.fringe_rate_override is None:
            people_without_type.add(appointment.person.alias)
        plan = {"appointment": appointment, "months": 0, "salary": ZERO, "fringe": ZERO, "tuition": ZERO}
        for month in iter_months(start, end):
            first, last = max(month, start), min(month_end(month), end)
            if last < first:
                continue
            fraction = Decimal((last - first).days + 1) / Decimal((month_end(month) - month).days + 1)
            planned_here = ZERO
            if not is_covered(Category.SALARIES, appointment.person_id, month):
                pay = appointment.monthly_charge * fraction
                fringe_rate = appointment.fringe_rate_override
                if fringe_rate is None:
                    fringe_rate = rate_for(person_type, "fringe_rate", month)
                benefits = pay * fringe_rate / 100
                salary_line.planned += pay
                fringe_line.planned += benefits
                plan["salary"] += pay
                plan["fringe"] += benefits
                plan["months"] += 1
                planned_here += pay + benefits
                if month < current:
                    past_planned_months.add(month)
                if salary_line.category.pk in eligible_ids:
                    monthly_planned_eligible[month] += pay
                if fringe_line.category.pk in eligible_ids:
                    monthly_planned_eligible[month] += benefits
            tuition_rate = appointment.tuition_monthly_override
            if tuition_rate is None:
                tuition_rate = rate_for(person_type, "tuition_monthly", month)
            tuition = tuition_rate * appointment.effort_pct / 100 * fraction
            if tuition and not is_covered(Category.TUITION, appointment.person_id, month):
                tuition_line.planned += tuition
                plan["tuition"] += tuition
                planned_here += tuition
            monthly_planned[month] += planned_here
        plan_rows.append(plan)

    # --- Pending entries
    pending_entries = list(worktag.pending_entries.filter(status=PendingEntry.Status.PENDING))
    for entry in pending_entries:
        lines[entry.category_id].pending += entry.amount

    # --- F&A
    fa_line = line_for(Category.FA)
    eligible_lines = [line for pk, line in lines.items() if pk in eligible_ids]
    if fa_rate:
        if calculate_fa:
            calculated = sum((line.actual for line in eligible_lines), ZERO) * fa_rate
            fa_line.actual += calculated
            fa_line.estimated += calculated
            for month, base in monthly_eligible.items():
                monthly_actual[month] += base * fa_rate
        fa_line.pending += sum((line.pending for line in eligible_lines), ZERO) * fa_rate
        fa_line.planned += sum((line.planned for line in eligible_lines), ZERO) * fa_rate
        for month, base in monthly_planned_eligible.items():
            monthly_planned[month] += base * fa_rate

    ordered = [lines[c.pk] for c in categories]
    for line in ordered:
        line.round()
    total = Line(None)
    for line in ordered:
        total.budget += line.budget
        total.actual += line.actual
        total.pending += line.pending
        total.planned += line.planned
        total.estimated += line.estimated
    shown = [line for line in ordered if line.budget or line.actual or line.pending or line.planned]

    # --- Burn rate and runway
    # Average over the last 6 complete months, but not before the fund started or spending began,
    # so a fund with one month of history isn't averaged over six.
    window_start = add_months(current, -BURN_WINDOW_MONTHS)
    if worktag.start_date:
        window_start = max(window_start, month_start(worktag.start_date))
    if monthly_actual:
        window_start = max(window_start, min(monthly_actual))
    window = [m for m in iter_months(window_start, current - dt.timedelta(days=1))] if window_start < current else []
    burn_rate = cents(sum((monthly_actual.get(m, ZERO) for m in window), ZERO) / len(window)) if window else None
    months_left = None
    if worktag.end_date:
        months_left = max(ZERO, Decimal((worktag.end_date - today).days) / DAYS_PER_MONTH).quantize(Decimal("0.1"))
    runway = run_out = burn_end = None
    if burn_rate and burn_rate > 0:
        runway = max(ZERO, total.remaining / burn_rate).quantize(Decimal("0.1"))
        if runway <= MAX_RUNWAY_MONTHS:  # beyond this a run-out date is meaningless (and overflows date math)
            run_out = today + dt.timedelta(days=int(runway * DAYS_PER_MONTH))
        if months_left is not None:
            burn_end = cents(total.remaining - burn_rate * months_left)

    summary = FundSummary(
        worktag=worktag,
        lines=shown,
        total=total,
        burn_rate=burn_rate,
        months_left=months_left,
        runway_months=runway,
        run_out_date=run_out,
        burn_projected_end=burn_end,
        plan_rows=plan_rows,
        monthly_actual=dict(monthly_actual),
        monthly_planned=dict(monthly_planned),
        burn_months=len(window),
    )
    summary.alerts = _fund_alerts(
        summary, today, pending_entries, uncategorized, ignored_fa, people_without_type, unestimated_payroll, carried,
        past_planned_months, unlinked_salary_months, bool(appointments),
    )
    return summary


def _money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def _fund_alerts(
    s, today, pending_entries, uncategorized, ignored_fa, people_without_type, unestimated_payroll, carried,
    past_planned_months, unlinked_salary_months, has_appointments,
):
    w, total, alerts = s.worktag, s.total, []

    def add(level, message):
        alerts.append(Alert(level, message, w))

    if not total.budget:
        add("info", "No budget entered yet.")
    elif total.remaining < 0:
        add("danger", f"Over budget by {_money(-total.remaining)} (spent + pending).")
    elif total.projected_remaining < 0:
        add("danger", f"Projected to overspend by {_money(-total.projected_remaining)} including planned payroll.")
    if total.budget:
        for line in s.lines:
            if line.budget and line.projected_remaining < 0 and total.projected_remaining >= 0:
                add("warn", f"{line.category.name} projected over its budget by {_money(-line.projected_remaining)}.")
            elif not line.budget and line.projected > 0:
                add("info", f"{line.category.name} has spending ({_money(line.projected)}) but no budget line.")
    if w.end_date and s.run_out_date and s.run_out_date < w.end_date and total.remaining > 0:
        basis = f"average of the last {s.burn_months} months" if s.burn_months > 1 else "based on one month of spending"
        add("warn", f"At the current burn rate ({_money(s.burn_rate)}/month, {basis}) money runs out around "
                    f"{s.run_out_date:%b %Y}, before the end date.")
    if w.end_date and w.is_active:
        days = (w.end_date - today).days
        if days < 0:
            add("info", f"Ended {w.end_date:%b %-d, %Y}. Mark it inactive once it's closed out.")
        elif days <= 90:
            unspent = total.projected_remaining
            if total.budget and unspent > total.budget * Decimal("0.1"):
                add("warn", f"Ends in {days} days with about {_money(unspent)} projected unspent.")
            else:
                add("info", f"Ends in {days} days.")
    for plan in s.plan_rows:
        appointment = plan["appointment"]
        if w.end_date and appointment.end_date > w.end_date:
            add("warn", f"{appointment.person.alias}'s appointment runs past the fund end date.")
    overdue = [e for e in pending_entries if e.expected_date and (today - e.expected_date).days > 30]
    if overdue:
        add("warn", f"{len(overdue)} pending item{'s' if len(overdue) != 1 else ''} more than 30 days past the "
                    "expected date. Reconcile or cancel.")
    if ignored_fa:
        add("warn", f"{_money(ignored_fa)} of imported F&A lines ignored because F&A is calculated from the rate.")
    if w.fa_rate and not any(line.category.code == Category.FA and line.budget for line in s.lines):
        add("info", "F&A rate is set but there's no F&A budget line.")
    if uncategorized:
        add("warn", f"{_money(uncategorized)} uncategorized (counted as Other). Assign categories under Review.")
    if people_without_type:
        add("warn", "Set a person type (fringe rate) for: " + ", ".join(sorted(people_without_type)) + ".")
    if unestimated_payroll:
        add("info", "Some payroll rows have no person, so their fringe wasn't estimated.")
    if unlinked_salary_months and has_appointments:
        months = ", ".join(f"{m:%b %Y}" for m in unlinked_salary_months[:4])
        more = f" and {len(unlinked_salary_months) - 4} more" if len(unlinked_salary_months) > 4 else ""
        add("info", f"Salary rows without a person ({months}{more}) count as covering every planned appointment "
                    "that month. Link them to people under Transactions for per-person projections.")
    if past_planned_months:
        first, last = min(past_planned_months), max(past_planned_months)
        span = f"{first:%b %Y}" if first == last else f"{first:%b %Y} – {last:%b %Y}"
        add("info", f"{len(past_planned_months)} past month{'s' if len(past_planned_months) != 1 else ''} ({span}) "
                    "of planned payroll have no payroll actuals yet. Import payroll, or set the fund's tracking "
                    "start date if that spending is already covered by an opening balance.")
    no_rates = sorted({name for (name, _), source in carried.items() if source is None})
    if no_rates:
        add("warn", "No fringe rates entered for: " + ", ".join(no_rates) + ". Add them under Settings.")
    reused = defaultdict(set)
    for (name, year), source in carried.items():
        if source is not None:
            reused[(year, source)].add(name)
    for (year, source), names in sorted(reused.items()):
        add("info", f"FY{year} rates aren't entered for {', '.join(sorted(names))}; using FY{source} rates. "
                    "Update them under Settings.")
    return alerts


def summarize_funds(worktags, today: dt.date | None = None) -> list[FundSummary]:
    categories = list(Category.objects.all())
    rates = RateTable()
    return [summarize_fund(worktag, today=today, categories=categories, rates=rates) for worktag in worktags]
