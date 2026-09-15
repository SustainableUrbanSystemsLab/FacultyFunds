from django.conf import settings
from django.contrib.auth.views import redirect_to_login
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


class RequireTwoFactorMiddleware:
    """Every page requires a logged-in user who has also passed the second factor."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        if path.startswith("/" + settings.STATIC_URL.lstrip("/")) or path == reverse("accounts:login"):
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
