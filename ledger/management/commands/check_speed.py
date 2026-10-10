import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.plugins.otp_totp.models import TOTPDevice

from ledger.models import Worktag


class Command(BaseCommand):
    help = (
        "Time the main pages as the owner sees them and show how much of each is database time. "
        "Run it on the server when pages feel slow."
    )

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(is_superuser=True, is_active=True).order_by("pk").first()
        device = TOTPDevice.objects.filter(user=user, confirmed=True).first() if user else None
        if device is None:
            raise CommandError("Create the owner account and set up 2FA first.")

        host = next((h for h in settings.ALLOWED_HOSTS if h and not h.startswith((".", "*"))), "localhost")
        client = Client(HTTP_HOST=host)
        client.force_login(user)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

        fund = Worktag.objects.order_by("pk").first()
        pages = [
            ("Funds", reverse("ledger:dashboard")),
            ("Transactions", reverse("ledger:transactions")),
            ("People", reverse("ledger:people")),
            ("Import", reverse("ledger:imports")),
            ("Settings", reverse("ledger:settings")),
        ]
        if fund:
            pages.insert(1, (f"Fund {fund.code}", reverse("ledger:fund_detail", args=[fund.pk])))

        self.stdout.write(f"{'page':<24}{'first':>9}{'again':>9}{'database':>10}{'queries':>9}")
        slowest = None
        try:
            for name, url in pages:
                started = time.perf_counter()
                client.get(url, secure=True)
                first = (time.perf_counter() - started) * 1000
                with CaptureQueriesContext(connection) as queries:
                    started = time.perf_counter()
                    response = client.get(url, secure=True)
                    again = (time.perf_counter() - started) * 1000
                if response.status_code != 200:
                    self.stdout.write(f"{name:<24}  HTTP {response.status_code}")
                    continue
                database = sum(float(q["time"]) for q in queries.captured_queries) * 1000
                self.stdout.write(
                    f"{name:<24}{first:>7.0f}ms{again:>7.0f}ms{database:>8.0f}ms{len(queries.captured_queries):>9}"
                )
                if slowest is None or again > slowest[1]:
                    slowest = (name, again, database)
        finally:
            client.logout()
        if slowest:
            name, again, database = slowest
            share = database / again * 100 if again else 0
            self.stdout.write(
                f"\nSlowest: {name} at {again:.0f} ms, {share:.0f}% of it in the database. "
                + ("The database is the bottleneck." if share > 60 and again > 300 else "Time is spent in the app itself.")
            )
