import datetime as dt
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from ledger.models import (
    Appointment,
    Category,
    FiscalYearRate,
    Funding,
    PendingEntry,
    Person,
    PersonType,
    Transaction,
    Worktag,
    WorktagType,
)
from ledger.services.projections import summarize_fund, summarize_funds

PASSWORD = "correct horse battery staple"


def make_funds(count=6):
    """Several funds with spending, payroll, pending items and funding, like a real instance."""
    cats = {c.code: c for c in Category.objects.all()}
    gra = PersonType.objects.create(name="Perf GRA")
    FiscalYearRate.objects.create(person_type=gra, fiscal_year=2027, fringe_rate=Decimal("7.9"), tuition_monthly=Decimal("1596"))
    person = Person.objects.create(alias="PF", person_type=gra)
    funds = []
    for i in range(count):
        kind = (WorktagType.GRANT, WorktagType.GIFT, WorktagType.STATE)[i % 3]
        fund = Worktag.objects.create(code=f"PF{i:04d}", name=f"Fund {i}", type=kind, end_date=dt.date(2028, 6, 30) if kind == WorktagType.GRANT else None)
        Funding.objects.create(worktag=fund, date=dt.date(2026, 8, 1), amount=Decimal("50000"))
        for month in (8, 9):
            Transaction.objects.create(worktag=fund, category=cats["salaries"], person=person, date=dt.date(2026, month, 28), amount=Decimal("2000"))
            Transaction.objects.create(worktag=fund, category=cats["travel"], date=dt.date(2026, month, 10), amount=Decimal("300"))
        Appointment.objects.create(person=person, worktag=fund, start_date=dt.date(2026, 8, 1), end_date=dt.date(2027, 5, 31), monthly_salary=Decimal("2000"))
        PendingEntry.objects.create(worktag=fund, category=cats["supplies"], description="Order", amount=Decimal("400"))
        funds.append(fund)
    return funds


class BatchedSummaryTests(TestCase):
    today = dt.date(2026, 10, 9)

    def test_batched_summaries_match_one_by_one(self):
        funds = make_funds(6)
        batched = summarize_funds(Worktag.objects.order_by("pk"), today=self.today)
        for fund, together in zip(funds, batched):
            alone = summarize_fund(Worktag.objects.get(pk=fund.pk), today=self.today)
            for name in ("budget", "actual", "pending", "planned", "estimated"):
                self.assertEqual(getattr(together.total, name), getattr(alone.total, name), (fund.code, name))
            self.assertEqual(together.burn_rate, alone.burn_rate)
            self.assertEqual(together.end_date, alone.end_date)
            self.assertEqual([a.message for a in together.alerts], [a.message for a in alone.alerts])
            self.assertEqual(len(together.plan_rows), len(alone.plan_rows))

    def test_query_count_does_not_grow_with_the_number_of_funds(self):
        make_funds(2)
        with CaptureQueriesContext(connection) as few:
            summarize_funds(Worktag.objects.all(), today=self.today)
        make_funds_more = Worktag.objects.count()
        cats = Category.objects.get(code="travel")
        for i in range(10):
            fund = Worktag.objects.create(code=f"EX{i}", name="Extra")
            Funding.objects.create(worktag=fund, date=dt.date(2026, 8, 1), amount=Decimal("1000"))
            Transaction.objects.create(worktag=fund, category=cats, date=dt.date(2026, 9, 1), amount=Decimal("10"))
        with CaptureQueriesContext(connection) as many:
            summarize_funds(Worktag.objects.all(), today=self.today)
        self.assertEqual(len(few), len(many), f"{make_funds_more} funds vs {Worktag.objects.count()} funds")


class OwnerClientMixin:
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user("owner", password=PASSWORD, is_superuser=True)
        self.device = TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)

    def sign_in(self):
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})
        token = f"{totp(self.device.bin_key, self.device.step, self.device.t0, self.device.digits):06d}"
        self.client.post(reverse("accounts:verify"), {"token": token})


class TimingMiddlewareTests(OwnerClientMixin, TestCase):
    def test_signed_in_pages_report_where_the_time_went(self):
        self.sign_in()
        header = self.client.get(reverse("ledger:dashboard"))["Server-Timing"]
        self.assertIn("app;dur=", header)
        self.assertIn("db;dur=", header)
        self.assertRegex(header, r'desc="\d+ queries"')

    def test_anonymous_pages_get_no_timing_header(self):
        self.assertNotIn("Server-Timing", self.client.get(reverse("accounts:login")))

    def test_slow_requests_are_logged_with_database_time(self):
        self.sign_in()
        with override_settings(SLOW_REQUEST_SECONDS=0), self.assertLogs("facultyfunds.timing", "WARNING") as logs:
            self.client.get(reverse("ledger:people"))
        self.assertIn("slow request GET /people/", logs.output[0])
        self.assertIn("queries", logs.output[0])

    def test_fast_requests_are_not_logged(self):
        self.sign_in()
        with override_settings(SLOW_REQUEST_SECONDS=60), self.assertNoLogs("facultyfunds.timing", "WARNING"):
            self.client.get(reverse("ledger:people"))


class CheckSpeedCommandTests(OwnerClientMixin, TestCase):
    def test_reports_each_page(self):
        make_funds(3)
        out = StringIO()
        call_command("check_speed", stdout=out)
        text = out.getvalue()
        for page in ("Funds", "Transactions", "People", "Import", "Settings", "Slowest:"):
            self.assertIn(page, text)
        self.assertNotIn("HTTP 4", text)
        self.assertNotIn("HTTP 5", text)

    def test_needs_an_owner_with_two_factor(self):
        TOTPDevice.objects.all().delete()
        with self.assertRaisesMessage(CommandError, "set up 2FA"):
            call_command("check_speed", stdout=StringIO())


class DashboardQueryBudgetTests(OwnerClientMixin, TestCase):
    def test_dashboard_stays_cheap_with_many_funds(self):
        make_funds(12)
        self.sign_in()
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("ledger:dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(queries), 30, "the dashboard should not run queries per fund")


class WarmUpTests(SimpleTestCase):
    def test_warm_up_loads_everything_without_touching_the_database(self):
        from config.wsgi import warm_up

        warm_up()  # SimpleTestCase raises on any database access
