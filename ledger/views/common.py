from django.shortcuts import render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme


def safe_next(request, fallback: str) -> str:
    candidate = request.POST.get("next") or request.GET.get("next") or ""
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}):
        return candidate
    return reverse(fallback) if ":" in fallback else fallback


def confirm_page(request, *, title: str, detail: str = "", cancel_url: str, button: str = "Delete"):
    return render(
        request,
        "ledger/confirm.html",
        {"title": title, "detail": detail, "cancel_url": cancel_url, "button": button},
    )


def csv_safe(value):
    """Stop spreadsheet apps from treating exported text as a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
