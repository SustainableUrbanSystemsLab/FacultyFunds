import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from ledger import samples
from ledger.forms import WorktagForm
from django.utils import timezone

from ledger.models import (
    Appointment,
    Category,
    FiscalYearRate,
    ImportBatch,
    PendingEntry,
    Person,
    PersonType,
    Transaction,
    Worktag,
)
from ledger.services.rates import fiscal_year

PASSWORD = "correct horse battery staple"


class AuthFlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password=PASSWORD)

    def login(self):
        return self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})

    def test_anonymous_redirects_to_login(self):
        response = self.client.get(reverse("ledger:dashboard"))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next=/", fetch_redirect_response=False)

    def test_password_alone_is_not_enough(self):
        response = self.login()
        self.assertRedirects(response, reverse("accounts:setup"), fetch_redirect_response=False)
        response = self.client.get(reverse("ledger:transactions"))
        self.assertRedirects(response, reverse("accounts:setup"), fetch_redirect_response=False)

    def test_setup_then_verify(self):
        self.login()
        self.assertEqual(self.client.get(reverse("accounts:setup")).status_code, 200)
        device = TOTPDevice.objects.get(user=self.user, confirmed=False)
        token = f"{totp(device.bin_key, device.step, device.t0, device.digits):06d}"
        response = self.client.post(reverse("accounts:setup"), {"token": token})
        self.assertRedirects(response, reverse("accounts:security"), fetch_redirect_response=False)
        page = self.client.get(reverse("accounts:security"))
        self.assertEqual(len(page.context["codes"]), 10)
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 200)

    def test_wrong_code_rejected_and_setup_blocked_once_enrolled(self):
        TOTPDevice.objects.create(user=self.user, name="phone", confirmed=True)
        self.login()
        response = self.client.post(reverse("accounts:verify"), {"token": "000000"})
        self.assertContains(response, "Invalid or expired code")
        # Can't enroll a second authenticator without passing 2FA first.
        self.assertRedirects(self.client.get(reverse("accounts:setup")), reverse("accounts:verify"), fetch_redirect_response=False)

    def test_security_headers(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertIn("frame-ancestors 'none'", response["Content-Security-Policy"])
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.get("/healthz").content, b"ok")


class PageTests(TestCase):
    """Every page renders for a verified user, with data in place."""

    def setUp(self):
        user = get_user_model().objects.create_user("owner", password=PASSWORD)
        device = TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})
        token = f"{totp(device.bin_key, device.step, device.t0, device.digits):06d}"
        self.assertEqual(self.client.post(reverse("accounts:verify"), {"token": token}).status_code, 302)

        self.fund = Worktag.objects.create(code="GR00012345", name="Solar", fa_rate=Decimal("57.4"),
                                           start_date=dt.date(2026, 1, 1), end_date=dt.date(2027, 6, 30))
        travel = Category.objects.get(code="travel")
        self.person = Person.objects.create(alias="JD")
        self.appointment = Appointment.objects.create(person=self.person, worktag=self.fund, start_date=dt.date(2026, 8, 1),
                                                      end_date=dt.date(2027, 5, 31), monthly_salary=Decimal("2500"))
        self.txn = Transaction.objects.create(worktag=self.fund, category=travel, person=self.person,
                                              date=dt.date(2026, 8, 20), amount=Decimal("412.50"), description="Flight")
        Transaction.objects.create(date=dt.date(2026, 8, 21), amount=Decimal("10"), raw_worktags="Gift: GF00077777 Lab")
        self.pending = PendingEntry.objects.create(worktag=self.fund, category=travel, description="Trip", amount=Decimal("900"))

    def test_pages_render(self):
        urls = [
            reverse("ledger:dashboard"),
            reverse("ledger:fund_create") + "?code=GF00077777&type=gift",
            reverse("ledger:fund_detail", args=[self.fund.pk]),
            reverse("ledger:fund_edit", args=[self.fund.pk]),
            reverse("ledger:fund_delete", args=[self.fund.pk]),
            reverse("ledger:transactions") + "?fund=none&q=gift",
            reverse("ledger:transaction_create"),
            reverse("ledger:transaction_edit", args=[self.txn.pk]),
            reverse("ledger:review"),
            reverse("ledger:pending"),
            reverse("ledger:pending_create"),
            reverse("ledger:pending_reconcile", args=[self.pending.pk]),
            reverse("ledger:people"),
            reverse("ledger:person_create"),
            reverse("ledger:person_detail", args=[self.person.pk]),
            reverse("ledger:appointment_create") + f"?person={self.person.pk}",
            reverse("ledger:appointment_edit", args=[self.appointment.pk]),
            reverse("ledger:imports"),
            reverse("ledger:settings"),
            reverse("accounts:security"),
            reverse("accounts:password_change"),
        ]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertContains(self.client.get(reverse("ledger:review")), "GF00077777")
        export = self.client.get(reverse("ledger:fund_export", args=[self.fund.pk]))
        self.assertEqual(export["Content-Type"], "text/csv")

    def test_upload_map_preview_commit(self):
        upload = SimpleUploadedFile("expenses.xlsx", samples.workday_expense_xlsx())
        response = self.client.post(reverse("ledger:import_upload"), {"source": "workday_expense", "file": upload})
        batch = ImportBatch.objects.get()
        self.assertRedirects(response, reverse("ledger:import_map", args=[batch.pk]), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("ledger:import_map", args=[batch.pk])).status_code, 200)

        form = self.client.get(reverse("ledger:import_map", args=[batch.pk])).context["form"]
        data = {name: value for name, value in form.initial.items() if value not in (None, [], "")}
        data.update({"import_as": "actual", "skip_zero": "on"})
        response = self.client.post(reverse("ledger:import_map", args=[batch.pk]), data)
        self.assertRedirects(response, reverse("ledger:import_preview", args=[batch.pk]), fetch_redirect_response=False)
        preview = self.client.get(reverse("ledger:import_preview", args=[batch.pk]))
        self.assertContains(preview, "Import 4 rows")
        self.client.post(reverse("ledger:import_commit", args=[batch.pk]), {"save_profile": "on", "profile_name": "WD"})
        self.assertEqual(Transaction.objects.filter(batch=batch).count(), 4)
        self.assertEqual(self.client.get(reverse("ledger:import_preview", args=[batch.pk])).status_code, 200)

    def test_next_parameter_is_sanitized(self):
        response = self.client.get(reverse("ledger:transaction_create") + "?next=https://evil.example/x")
        self.assertNotContains(response, "evil.example")
        response = self.client.get(reverse("ledger:transaction_create") + "?next=/pending/")
        self.assertContains(response, 'href="/pending/"')

    @override_settings(FILE_UPLOAD_MAX_MEMORY_SIZE=2048)
    def test_oversized_upload_is_refused_before_parsing(self):
        client = Client(raise_request_exception=False)
        client.cookies = self.client.cookies
        upload = SimpleUploadedFile("big.csv", b"Date,Amount\n" + b"2026-01-01,1\n" * 500)
        response = client.post(reverse("ledger:import_upload"), {"source": "other_import", "file": upload})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "too large", status_code=400)
        self.assertFalse(ImportBatch.objects.exists())

    def test_worktag_form_rejects_codes_owned_by_another_fund(self):
        form = WorktagForm({"type": "gift", "code": "gr00012345", "name": "Dup", "fa_rate": "0", "fa_actuals": "calculate", "is_active": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("already belongs to GR00012345", form.errors["code"][0])
        form = WorktagForm({"type": "gift", "code": "GF1", "name": "Gift", "fa_rate": "0", "fa_actuals": "calculate",
                            "match_codes": "AWD1\nGR00012345", "is_active": "on"})
        self.assertFalse(form.is_valid())
        self.assertIn("match_codes", form.errors)

    def test_bulk_assign_and_settings_save(self):
        other = Transaction.objects.get(worktag__isnull=True)
        self.client.post(reverse("ledger:transactions_bulk"), {"ids": [other.pk], "worktag": self.fund.pk, "action": "assign"})
        other.refresh_from_db()
        self.assertEqual(other.worktag, self.fund)

        settings_page = self.client.get(reverse("ledger:settings"))
        formset = settings_page.context["person_type_formset"]
        data = {"which": "person_types"}
        for field in formset.management_form:
            data[field.html_name] = field.value()
        for form in formset.forms:  # post exactly what the browser would
            for field in form:
                value = field.value()
                if value is not None and value is not False:
                    data[field.html_name] = value
        data["pt-0-name"] = "Faculty"
        response = self.client.post(reverse("ledger:settings"), data)
        errors = response.context and response.context["person_type_formset"].errors
        self.assertEqual(response.status_code, 302, errors)

        # Rate grid: set this fiscal year's GRA fringe; the migrated FY2025 row stays.
        current = fiscal_year(timezone.localdate())
        rates_form = self.client.get(reverse("ledger:settings")).context["rates_form"]
        self.assertIn(current, rates_form.fiscal_years)
        gra = PersonType.objects.get(name="Graduate research assistant")
        data = {"which": "rates", "fy": [str(year) for year in rates_form.fiscal_years]}
        for field in rates_form:
            if field.value() is not None:
                data[field.html_name] = field.value()
        data[f"fringe_{gra.pk}_{current}"] = "8.25"
        response = self.client.post(reverse("ledger:settings"), data)
        self.assertEqual(response.status_code, 302, response.context and response.context["rates_form"].errors)
        self.assertEqual(FiscalYearRate.objects.get(person_type=gra, fiscal_year=current).fringe_rate, Decimal("8.25"))
        self.assertEqual(FiscalYearRate.objects.get(person_type=gra, fiscal_year=2025).fringe_rate, Decimal("7.9"))
