from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django_otp.admin import OTPAdminSite

# The admin also requires a verified second factor.
admin.site.__class__ = OTPAdminSite
admin.site.site_header = "Faculty Funds admin"
admin.site.site_title = "Faculty Funds admin"

urlpatterns = [
    path("accounts/webauthn/", include("django_otp_webauthn.urls")),
    path("accounts/", include("accounts.urls")),
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("ledger.urls")),
]
