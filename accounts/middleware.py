import functools
import logging
import time

from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.db import connection
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse

CSP = "; ".join(
    [
        "default-src 'self'",
        "img-src 'self' data:",
        "style-src 'self'",
        "script-src 'self'",
        "font-src 'self'",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ]
)
# The Django admin uses a few inline styles.
ADMIN_CSP = CSP.replace("style-src 'self'", "style-src 'self' 'unsafe-inline'")


timing_log = logging.getLogger("facultyfunds.timing")


def has_confirmed_totp(user) -> bool:
    from django_otp.plugins.otp_totp.models import TOTPDevice

    return TOTPDevice.objects.filter(user=user, confirmed=True).exists()


class HealthCheckMiddleware:
    """Answer platform health checks before host validation and HTTPS redirects."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)


class RequestTimingMiddleware:
    """Show where each page's time goes, so a slow reload can be traced to the database or the app.

    Logged-in users get a Server-Timing header (see the browser's network panel). Requests slower than
    SLOW_REQUEST_SECONDS are logged with their database time and query count.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        stats = {"queries": 0, "db": 0.0}

        def timed(execute, sql, params, many, context):
            started = time.perf_counter()
            try:
                return execute(sql, params, many, context)
            finally:
                stats["queries"] += 1
                stats["db"] += time.perf_counter() - started

        started = time.perf_counter()
        with connection.execute_wrapper(timed):
            response = self.get_response(request)
        total = time.perf_counter() - started
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            response.headers["Server-Timing"] = (
                f'app;dur={(total - stats["db"]) * 1000:.0f};desc="Django", '
                f'db;dur={stats["db"] * 1000:.0f};desc="{stats["queries"]} queries"'
            )
        if total >= settings.SLOW_REQUEST_SECONDS:
            timing_log.warning(
                "slow request %s %s -> %s in %.2fs (database %.2fs, %d queries)",
                request.method, request.path, response.status_code, total, stats["db"], stats["queries"],
            )
        return response


class SessionTouchMiddleware:
    """Sliding idle timeout without a database write on every request.

    Saving the session pushes its expiry back. Doing that on every click makes each page wait on a
    database commit, so only touch it when it hasn't been saved for a few minutes. The idle timeout
    stays accurate to within that interval.
    """

    INTERVAL_SECONDS = 300

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            now = int(time.time())
            if now - request.session.get("_touched", 0) >= self.INTERVAL_SECONDS:
                request.session["_touched"] = now
        return self.get_response(request)


@functools.cache
def passkey_sign_in_paths() -> frozenset[str]:
    """Passkey sign-in endpoints: used before sign-in (passkey only) and after a password (as the second factor)."""
    return frozenset(
        reverse(name)
        for name in (
            "otp_webauthn:credential-authentication-begin",
            "otp_webauthn:credential-authentication-complete",
            "otp_webauthn:js-i18n-catalog",
        )
    )


class RequireTwoFactorMiddleware:
    """Every page requires a logged-in user who has also passed the second factor."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if (
            path.startswith("/" + settings.STATIC_URL.lstrip("/"))
            or path == reverse("accounts:login")
            or path in passkey_sign_in_paths()
        ):
            return self.get_response(request)

        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("accounts:login"))
        if user.is_verified():
            return self.get_response(request)

        # Password accepted, second factor still pending.
        if path in {reverse("accounts:logout"), reverse("accounts:verify"), reverse("accounts:setup")}:
            return self.get_response(request)
        return redirect("accounts:verify" if has_confirmed_totp(user) else "accounts:setup")


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_admin = request.path_info.startswith("/" + settings.ADMIN_URL)
        response.headers.setdefault("Content-Security-Policy", ADMIN_CSP if is_admin else CSP)
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if not request.path_info.startswith("/" + settings.STATIC_URL.lstrip("/")):
            # Financial data: never cache pages in the browser or proxies.
            response.headers["Cache-Control"] = "no-store"
        return response
