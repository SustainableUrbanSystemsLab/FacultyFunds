from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.OwnerLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("2fa/", views.verify, name="verify"),
    path("2fa/setup/", views.setup, name="setup"),
    path("security/", views.security, name="security"),
    path("password/", views.PasswordChange.as_view(), name="password_change"),
]
