"""Server-side SVG chart geometry (no JavaScript needed, works with a strict CSP)."""

import datetime as dt
from decimal import Decimal

from django.utils import timezone

from .projections import FundSummary, add_months, iter_months, month_start


def short_money(value) -> str:
    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{sign}${value / 1_000:.0f}k"
    return f"{sign}${value:.0f}"


def burn_chart(summary: FundSummary, today: dt.date | None = None, width: int = 760, height: int = 260) -> dict | None:
    """Cumulative spending vs. budget: actual line to this month, dashed projection to the end date."""
    worktag = summary.worktag
    today = today or timezone.localdate()
    current = month_start(today)
    known = sorted(set(summary.monthly_actual) | set(summary.monthly_planned))
    start = month_start(worktag.start_date) if worktag.start_date else (known[0] if known else current)
    end = month_start(worktag.end_date) if worktag.end_date else max([add_months(current, 6), *known[-1:]])
    if end <= start:
        end = add_months(start, 1)
    months = list(iter_months(start, end))
    index = {month: i for i, month in enumerate(months)}

    cumulative = sum((v for m, v in summary.monthly_actual.items() if m < start), Decimal(0))
    actual_points = []
    for month in months:
        if month > current:
            break
        cumulative += summary.monthly_actual.get(month, Decimal(0))
        actual_points.append((month, cumulative))

    projected_points = []
    anchor = max(current, start)
    if anchor <= end:
        projected = cumulative + summary.total.pending
        projected += sum((v for m, v in summary.monthly_planned.items() if m <= anchor), Decimal(0))
        projected_points.append((anchor, projected))
        for month in months:
            if month > anchor:
                projected += summary.monthly_planned.get(month, Decimal(0))
                projected_points.append((month, projected))

    values = [summary.total.budget, Decimal(1), *(v for _, v in actual_points), *(v for _, v in projected_points)]
    top_value = float(max(values)) * 1.08
    left, right, top, bottom = 64, 16, 14, 30
    plot_w, plot_h = width - left - right, height - top - bottom
    steps = max(1, len(months) - 1)

    def x(month):
        return left + plot_w * index[month] / steps

    def y(value):
        return top + plot_h * (1 - max(float(value), 0) / top_value)

    def path(points):
        return "M" + " L".join(f"{x(m):.1f},{y(v):.1f}" for m, v in points) if points else ""

    label_every = max(1, round(len(months) / 6))
    return {
        "width": width,
        "height": height,
        "plot_left": left,
        "plot_right": width - right,
        "plot_bottom": top + plot_h,
        "budget_y": round(y(summary.total.budget), 1) if summary.total.budget else None,
        "actual_path": path(actual_points),
        "projected_path": path(projected_points),
        "today_x": round(x(current), 1) if start <= current <= end else None,
        "x_ticks": [(round(x(m), 1), f"{m:%b %y}") for i, m in enumerate(months) if i % label_every == 0],
        "y_ticks": [(round(y(top_value * i / 4), 1), short_money(top_value * i / 4)) for i in range(5)],
    }
