from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _decimal(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter
def money(value, with_cents=False):
    amount = _decimal(value)
    if amount is None:
        return "—"
    text = f"${abs(amount):,.2f}" if with_cents else f"${abs(amount):,.0f}"
    return f"−{text}" if amount < 0 else text


@register.filter
def money_cents(value):
    return money(value, True)


@register.filter
def neg_class(value):
    amount = _decimal(value)
    return "neg" if amount is not None and amount < 0 else ""


@register.filter
def percent(value):
    amount = _decimal(value)
    return "—" if amount is None else f"{amount:.0f}%"


@register.filter
def trim_decimal(value):
    """57.400 -> 57.4, 100.00 -> 100."""
    amount = _decimal(value)
    return "—" if amount is None else format(amount.normalize(), "f")


@register.filter
def runway(months):
    amount = _decimal(months)
    if amount is None:
        return "—"
    return "10+ yr" if amount > 120 else f"{amount} mo"


@register.filter
def sum_values(values):
    return sum(values)


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if hasattr(mapping, "get") else None


@register.filter
def budget_bar(line):
    """Widths (0–100) for a stacked bar: spent, then committed (pending + planned)."""
    budget = line.budget
    if not budget or budget <= 0:
        return {"empty": True, "spent": "0", "committed": "0", "over": line.projected > 0}
    spent = min(max(line.actual / budget, Decimal(0)), Decimal(1))
    committed = min(max((line.actual + line.committed) / budget, Decimal(0)), Decimal(1)) - spent
    return {
        "empty": False,
        "spent": f"{spent * 100:.2f}",
        "committed": f"{committed * 100:.2f}",
        "committed_x": f"{spent * 100:.2f}",
        "over": line.projected > budget,
    }


@register.simple_tag(takes_context=True)
def query_with(context, **changes):
    """Current query string with some parameters replaced (for pagination and filters)."""
    params = context["request"].GET.copy()
    for key, value in changes.items():
        if value in (None, ""):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else "?"
