import base64
import io

import qrcode
import qrcode.image.svg
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView, PasswordChangeView
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods
from django_otp import login as otp_login
from django_otp import match_token
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import TokenForm
from .middleware import has_confirmed_totp

NEXT_KEY = "post_2fa_next"
FAILURES_KEY = "otp_failures"
FRESH_CODES_KEY = "fresh_backup_codes"
MAX_OTP_FAILURES = 10
BACKUP_CODE_COUNT = 10


class OwnerLoginView(LoginView):
    template_name = "accounts/login.html"
    redirect_authenticated_user = True

    def form_valid(self, form):
        super().form_valid(form)
        next_url = self.get_redirect_url()  # empty unless a safe ?next= was given
        if next_url:
            self.request.session[NEXT_KEY] = next_url
        return redirect("accounts:verify" if has_confirmed_totp(form.get_user()) else "accounts:setup")


class PasswordChange(PasswordChangeView):
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("accounts:security")

    def form_valid(self, form):
        messages.success(self.request, "Password changed.")
        return super().form_valid(form)


def _next_url(request) -> str:
    next_url = request.session.pop(NEXT_KEY, "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return next_url
    return reverse("ledger:dashboard")


def _record_failure(request):
    """Count bad codes per session; sign out after too many (django-otp also throttles per device)."""
    failures = request.session.get(FAILURES_KEY, 0) + 1
    request.session[FAILURES_KEY] = failures
    if failures >= MAX_OTP_FAILURES:
        logout(request)
        messages.error(request, "Too many invalid codes. Sign in again.")
        return redirect("accounts:login")
    return None


def regenerate_backup_codes(user) -> list[str]:
    device, _ = StaticDevice.objects.get_or_create(user=user, name="backup codes", defaults={"confirmed": True})
    device.token_set.all().delete()
    codes = [StaticToken.random_token() for _ in range(BACKUP_CODE_COUNT)]
    StaticToken.objects.bulk_create(StaticToken(device=device, token=code) for code in codes)
    return codes


@require_http_methods(["GET", "POST"])
def verify(request):
    user = request.user
    if user.is_verified():
        return redirect("ledger:dashboard")
    if not has_confirmed_totp(user):
        return redirect("accounts:setup")

    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        device = match_token(user, form.cleaned_data["token"])
        if device is not None:
            otp_login(request, device)
            request.session.pop(FAILURES_KEY, None)
            if isinstance(device, StaticDevice):
                left = device.token_set.count()
                messages.warning(request, f"Backup code used. {left} backup codes left.")
            return redirect(_next_url(request))
        if response := _record_failure(request):
            return response
        form.add_error("token", "Invalid or expired code.")
    return render(request, "accounts/verify.html", {"form": form})


@require_http_methods(["GET", "POST"])
def setup(request):
    user = request.user
    replacing = has_confirmed_totp(user)
    if replacing and not user.is_verified():
        # An existing authenticator can only be replaced after passing 2FA with it.
        return redirect("accounts:verify")

    device = TOTPDevice.objects.filter(user=user, confirmed=False).first()
    if device is None:
        device = TOTPDevice.objects.create(user=user, name="authenticator app", confirmed=False)

    form = TokenForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if device.verify_token(form.cleaned_data["token"]):
            TOTPDevice.objects.filter(user=user).exclude(pk=device.pk).delete()
            device.confirmed = True
            device.save()
            otp_login(request, device)
            request.session.pop(FAILURES_KEY, None)
            request.session[FRESH_CODES_KEY] = regenerate_backup_codes(user)
            messages.success(request, "Two-factor authentication is on. Save your backup codes now.")
            return redirect("accounts:security")
        if response := _record_failure(request):
            return response
        form.add_error("token", "That code didn't match. Check your phone's clock and try again.")

    buffer = io.BytesIO()
    qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    qr_data_uri = "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()
    secret = base64.b32encode(device.bin_key).decode()
    context = {
        "form": form,
        "qr_data_uri": qr_data_uri,
        "secret": " ".join(secret[i : i + 4] for i in range(0, len(secret), 4)),
        "replacing": replacing,
    }
    return render(request, "accounts/setup.html", context)


@require_http_methods(["GET", "POST"])
def security(request):
    codes = request.session.pop(FRESH_CODES_KEY, None)
    if request.method == "POST" and request.POST.get("action") == "regenerate":
        codes = regenerate_backup_codes(request.user)
        messages.success(request, "New backup codes generated. Old codes no longer work.")
    context = {
        "codes": codes,
        "remaining": StaticToken.objects.filter(device__user=request.user).count(),
        "totp_device": TOTPDevice.objects.filter(user=request.user, confirmed=True).first(),
    }
    return render(request, "accounts/security.html", context)
