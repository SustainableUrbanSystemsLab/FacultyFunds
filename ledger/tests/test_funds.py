import datetime as dt
import importlib
from decimal import Decimal

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice

from ledger.fiscal import fiscal_year, fiscal_year_bounds
from ledger.forms import AppointmentForm, WorktagForm
from ledger.models import (
    Appointment,
    BudgetLine,
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
from ledger.services.projections import summarize_fund
from ledger.services.rules import describe_code

PASSWORD = "correct horse battery staple"


class FiscalYearTests(SimpleTestCase):
    def test_bounds(self):
        self.assertEqual(fiscal_year_bounds(2027), (dt.date(2026, 7, 1), dt.date(2027, 6, 30)))
        self.assertEqual(fiscal_year(dt.date(2027, 6, 30)), 2027)
        self.assertEqual(fiscal_year(dt.date(2027, 7, 1)), 2028)


class FundKindTests(TestCase):
    today = dt.date(2026, 9, 14)  # FY2027: July 1, 2026 to June 30, 2027

    def setUp(self):
        self.cat = {c.code: c for c in Category.objects.all()}

    def spend(self, fund, code, day, amount, **extra):
        return Transaction.objects.create(
            worktag=fund, category=self.cat[code], date=day, amount=Decimal(amount), **extra
        )

    def test_gift_total_is_the_sum_of_funding_and_never_lapses(self):
        fund = Worktag.objects.create(code="DE00099999", name="Example Fellow Award", type=WorktagType.GIFT)
        Funding.objects.create(worktag=fund, date=dt.date(2025, 9, 1), amount=Decimal("2000"))
        Funding.objects.create(worktag=fund, date=dt.date(2026, 8, 1), amount=Decimal("1500"), description="Top-up")
        self.spend(fund, "supplies", dt.date(2025, 10, 1), "300")  # previous fiscal year still counts
        self.spend(fund, "travel", dt.date(2026, 8, 20), "200")
        s = summarize_fund(fund, today=self.today)
        self.assertEqual(s.total.budget, Decimal("3500.00"))
        self.assertEqual(s.total.actual, Decimal("500.00"))
        self.assertEqual(s.total.remaining, Decimal("3000.00"))
        self.assertIsNone(s.end_date)
        self.assertEqual(s.period_label, "")
        self.assertFalse(s.has_category_budgets)
        # No per-category budgets, so no "has spending but no budget line" noise.
        self.assertFalse(any("no budget line" in a.message for a in s.alerts))

    def test_state_fund_counts_only_the_current_fiscal_year(self):
        fund = Worktag.objects.create(code="CC0001234", name="Department", type=WorktagType.STATE)
        Funding.objects.create(worktag=fund, date=dt.date(2025, 8, 1), amount=Decimal("10000"), description="FY2026")
        Funding.objects.create(worktag=fund, date=dt.date(2026, 8, 1), amount=Decimal("15000"), description="FY2027")
        Funding.objects.create(worktag=fund, date=dt.date(2027, 8, 1), amount=Decimal("99999"), description="FY2028")
        self.spend(fund, "supplies", dt.date(2026, 3, 1), "4000")  # last fiscal year: lapsed, not counted
        self.spend(fund, "supplies", dt.date(2026, 8, 15), "3000")
        s = summarize_fund(fund, today=self.today)
        self.assertEqual(s.period_label, "FY2027")
        self.assertEqual((s.start_date, s.end_date), (dt.date(2026, 7, 1), dt.date(2027, 6, 30)))
        self.assertEqual(s.total.budget, Decimal("15000.00"))
        self.assertEqual(s.total.actual, Decimal("3000.00"))
        self.assertEqual(s.total.remaining, Decimal("12000.00"))

    def test_state_fund_warns_about_money_that_will_lapse(self):
        fund = Worktag.objects.create(code="CC1", name="Department", type=WorktagType.STATE)
        Funding.objects.create(worktag=fund, date=dt.date(2026, 8, 1), amount=Decimal("10000"))
        self.spend(fund, "supplies", dt.date(2027, 1, 10), "1000")
        s = summarize_fund(fund, today=dt.date(2027, 3, 15))
        self.assertTrue(any("lapse" in a.message and a.level == "warn" for a in s.alerts), s.alerts)

    def test_state_fund_without_allocation_for_this_year_says_so(self):
        fund = Worktag.objects.create(code="CC2", name="Department", type=WorktagType.STATE)
        Funding.objects.create(worktag=fund, date=dt.date(2025, 8, 1), amount=Decimal("10000"))  # FY2026 only
        s = summarize_fund(fund, today=self.today)
        self.assertEqual(s.total.budget, Decimal("0.00"))
        self.assertTrue(any("No allocation entered for FY2027" in a.message for a in s.alerts))

    def test_state_fund_payroll_is_clipped_to_the_fiscal_year(self):
        fund = Worktag.objects.create(code="CC3", name="Department", type=WorktagType.STATE)
        Funding.objects.create(worktag=fund, date=dt.date(2026, 8, 1), amount=Decimal("50000"))
        person = Person.objects.create(alias="AB")
        Appointment.objects.create(
            person=person, worktag=fund, start_date=dt.date(2027, 4, 1), end_date=dt.date(2027, 12, 31),
            monthly_salary=Decimal("1000"),
        )
        s = summarize_fund(fund, today=self.today)
        self.assertEqual(s.total.planned, Decimal("3000.00"))  # April, May, June 2027; the rest is next year's money

    def test_category_budgets_that_disagree_with_the_award_are_flagged(self):
        fund = Worktag.objects.create(code="GR5", name="Grant", type=WorktagType.GRANT)
        Funding.objects.create(worktag=fund, date=dt.date(2026, 1, 1), amount=Decimal("100000"))
        BudgetLine.objects.create(worktag=fund, category=self.cat["salaries"], amount=Decimal("70000"))
        s = summarize_fund(fund, today=self.today)
        self.assertEqual(s.total.budget, Decimal("100000.00"))
        self.assertTrue(s.has_category_budgets)
        self.assertTrue(any("Category budgets add up to $70,000" in a.message for a in s.alerts))

    def test_food_on_a_federal_grant_is_flagged_but_not_on_gifts(self):
        grant = Worktag.objects.create(code="GR6", name="Grant", type=WorktagType.GRANT)
        gift = Worktag.objects.create(code="GF6", name="Gift", type=WorktagType.GIFT)
        for fund in (grant, gift):
            Funding.objects.create(worktag=fund, date=dt.date(2026, 1, 1), amount=Decimal("5000"))
            self.spend(fund, "meals", dt.date(2026, 8, 20), "120")
        self.assertTrue(any("food and meals" in a.message for a in summarize_fund(grant, today=self.today).alerts))
        self.assertFalse(any("food and meals" in a.message for a in summarize_fund(gift, today=self.today).alerts))

    def test_pending_without_a_date_counts_for_a_state_fund(self):
        fund = Worktag.objects.create(code="CC4", name="Department", type=WorktagType.STATE)
        Funding.objects.create(worktag=fund, date=dt.date(2026, 8, 1), amount=Decimal("5000"))
        PendingEntry.objects.create(worktag=fund, category=self.cat["travel"], description="Trip", amount=Decimal("800"))
        PendingEntry.objects.create(
            worktag=fund, category=self.cat["travel"], description="Next year", amount=Decimal("900"),
            expected_date=dt.date(2027, 9, 1),
        )
        s = summarize_fund(fund, today=self.today)
        self.assertEqual(s.total.pending, Decimal("800.00"))

    def test_meals_category_and_rule_exist_after_migrating(self):
        meals = Category.objects.get(code="meals")
        self.assertEqual(meals.name, "Meals & hospitality")
        self.assertTrue(meals.rules.exists())

    def test_old_fund_types_and_budgets_are_carried_over(self):
        migration = importlib.import_module("ledger.migrations.0005_simplify_funds")
        startup = Worktag.objects.create(code="CC9", name="Startup", type="cost_center", start_date=dt.date(2025, 1, 1))
        BudgetLine.objects.create(worktag=startup, category=self.cat["other"], amount=Decimal("150000"))
        BudgetLine.objects.create(worktag=startup, category=self.cat["salaries"], amount=Decimal("20000"))
        grant = Worktag.objects.create(code="GR9", name="Grant", type="grant")
        migration.convert_funds(django_apps, None)
        startup.refresh_from_db()
        grant.refresh_from_db()
        self.assertEqual(startup.type, WorktagType.GIFT)
        self.assertEqual(grant.type, WorktagType.GRANT)
        entry = Funding.objects.get(worktag=startup)
        self.assertEqual((entry.amount, entry.date), (Decimal("170000"), dt.date(2025, 1, 1)))
        self.assertFalse(Funding.objects.filter(worktag=grant).exists())


class HourlyAppointmentTests(TestCase):
    def setUp(self):
        self.fund = Worktag.objects.create(code="GR7", name="Grant")
        Funding.objects.create(worktag=self.fund, date=dt.date(2026, 1, 1), amount=Decimal("50000"))
        self.student_type = PersonType.objects.create(name="Test undergrad")
        FiscalYearRate.objects.create(person_type=self.student_type, fiscal_year=2027, fringe_rate=Decimal("2"))
        self.person = Person.objects.create(alias="UG", person_type=self.student_type)

    def test_monthly_charge_is_rate_times_hours_times_52_over_12(self):
        appointment = Appointment(
            kind=Appointment.Kind.HOURLY, hourly_rate=Decimal("15"), hours_per_week=Decimal("10"),
        )
        self.assertEqual(appointment.monthly_charge.quantize(Decimal("0.01")), Decimal("650.00"))

    def test_hourly_appointment_is_projected_with_fringe(self):
        Appointment.objects.create(
            person=self.person, worktag=self.fund, kind=Appointment.Kind.HOURLY, hourly_rate=Decimal("15"),
            hours_per_week=Decimal("12"), start_date=dt.date(2026, 10, 1), end_date=dt.date(2026, 12, 31),
        )
        s = summarize_fund(self.fund, today=dt.date(2026, 9, 14))
        pay = Decimal("15") * Decimal("12") * Decimal("52") / 12 * 3
        self.assertEqual(s.total.planned, (pay * Decimal("1.02")).quantize(Decimal("0.01")))

    def form(self, **overrides):
        data = {
            "person": self.person.pk, "worktag": self.fund.pk, "kind": "hourly",
            "start_date": "2026-10-01", "end_date": "2026-12-31", "effort_pct": "100",
        }
        data.update(overrides)
        return AppointmentForm(data)

    def test_hourly_requires_rate_and_hours(self):
        form = self.form()
        self.assertFalse(form.is_valid())
        self.assertIn("hourly_rate", form.errors)
        self.assertIn("hours_per_week", form.errors)

    def test_hourly_form_saves_without_a_salary(self):
        form = self.form(hourly_rate="15.50", hours_per_week="8", effort_pct="40")
        self.assertTrue(form.is_valid(), form.errors)
        appointment = form.save()
        self.assertEqual(appointment.monthly_salary, 0)
        self.assertEqual(appointment.effort_pct, Decimal("100"))

    def test_monthly_kind_still_needs_a_salary(self):
        form = self.form(kind="regular")
        self.assertFalse(form.is_valid())
        self.assertIn("monthly_salary", form.errors)
        self.assertTrue(self.form(kind="regular", monthly_salary="2500").is_valid())


class WorktagFormTests(TestCase):
    def data(self, **extra):
        data = {"type": "gift", "code": "de00099999", "name": "Fellow award", "fa_rate": "0", "fa_actuals": "calculate",
                "is_active": "on"}
        data.update(extra)
        return data

    def test_creating_with_an_amount_adds_the_first_funding_entry(self):
        form = WorktagForm(self.data(amount="2000", funding_date="2026-09-01"))
        self.assertTrue(form.is_valid(), form.errors)
        fund = form.save()
        self.assertEqual(fund.code, "DE00099999")
        entry = fund.funding.get()
        self.assertEqual((entry.amount, entry.date), (Decimal("2000"), dt.date(2026, 9, 1)))

    def test_amount_is_optional_and_not_offered_when_editing(self):
        fund = WorktagForm(self.data()).save()
        self.assertEqual(fund.funding.count(), 0)
        editing = WorktagForm(instance=fund)
        self.assertNotIn("amount", editing.fields)
        self.assertNotIn("funding_date", editing.fields)

    def test_code_prefixes_suggest_a_fund_type(self):
        self.assertEqual(describe_code("GR00012345", "")[0], WorktagType.GRANT)
        self.assertEqual(describe_code("GF00077777", "")[0], WorktagType.GIFT)
        self.assertEqual(describe_code("CC0001234", "")[0], WorktagType.STATE)
        self.assertEqual(describe_code("PJ0001234", "")[0], "")  # project: you choose


class FundPagesTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("owner", password=PASSWORD)
        device = TOTPDevice.objects.create(user=user, name="phone", confirmed=True)
        self.client.post(reverse("accounts:login"), {"username": "owner", "password": PASSWORD})
        token = f"{totp(device.bin_key, device.step, device.t0, device.digits):06d}"
        self.client.post(reverse("accounts:verify"), {"token": token})
        today = dt.date.today()
        self.grant = Worktag.objects.create(code="GR1", name="Example grant", type=WorktagType.GRANT, sponsor="Example Agency",
                                            end_date=today + dt.timedelta(days=400))
        self.gift = Worktag.objects.create(code="DE00099999", name="Fellow award", type=WorktagType.GIFT)
        self.state = Worktag.objects.create(code="CC1", name="Department", type=WorktagType.STATE)
        for fund, amount in ((self.grant, "100000"), (self.gift, "2000"), (self.state, "15000")):
            Funding.objects.create(worktag=fund, date=today, amount=Decimal(amount))

    def test_dashboard_groups_funds_by_kind(self):
        page = self.client.get(reverse("ledger:dashboard"))
        for label in ("Federal grants", "Foundation and gift funds", "State funds"):
            self.assertContains(page, label)

    def test_main_navigation_has_five_sections(self):
        page = self.client.get(reverse("ledger:dashboard")).content.decode()
        nav = page[page.index('class="mainnav"'):page.index("</nav>", page.index('class="mainnav"'))]
        for label in ("Funds", "Transactions", "People", "Import", "Settings"):
            self.assertIn(f"<span>{label}</span>", nav)
        for gone in ("Review", "Pending"):
            self.assertNotIn(f"<span>{gone}</span>", nav)

    def test_transaction_tabs_and_settings_security_link(self):
        for name in ("ledger:transactions", "ledger:pending", "ledger:review"):
            page = self.client.get(reverse(name))
            self.assertContains(page, 'aria-label="Transactions sections"')
            self.assertContains(page, "Needs review")
        self.assertContains(self.client.get(reverse("ledger:settings")), reverse("accounts:security"))

    def test_fund_detail_shows_funding_for_each_kind(self):
        for fund in (self.grant, self.gift, self.state):
            page = self.client.get(reverse("ledger:fund_detail", args=[fund.pk]))
            self.assertContains(page, "Add funding")
        state_page = self.client.get(reverse("ledger:fund_detail", args=[self.state.pk]))
        self.assertContains(state_page, "lapses")

    def test_add_edit_and_delete_funding(self):
        url = reverse("ledger:funding_create", args=[self.gift.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        response = self.client.post(url, {"date": "2026-10-01", "amount": "500", "description": "Top-up"})
        self.assertRedirects(response, self.gift.get_absolute_url(), fetch_redirect_response=False)
        entry = self.gift.funding.get(description="Top-up")
        self.assertEqual(self.client.get(reverse("ledger:funding_edit", args=[entry.pk])).status_code, 200)
        self.client.post(reverse("ledger:funding_edit", args=[entry.pk]), {"date": "2026-10-01", "amount": "750"})
        entry.refresh_from_db()
        self.assertEqual(entry.amount, Decimal("750"))
        self.assertEqual(self.client.get(reverse("ledger:funding_delete", args=[entry.pk])).status_code, 200)
        self.client.post(reverse("ledger:funding_delete", args=[entry.pk]))
        self.assertFalse(Funding.objects.filter(pk=entry.pk).exists())

    def test_creating_a_fund_through_the_form_records_the_amount(self):
        response = self.client.post(reverse("ledger:fund_create"), {
            "type": "gift", "code": "de777", "name": "Gift", "fa_rate": "0", "fa_actuals": "calculate",
            "is_active": "on", "amount": "2000", "funding_date": "2026-09-01",
        })
        fund = Worktag.objects.get(code="DE777")
        self.assertRedirects(response, fund.get_absolute_url(), fetch_redirect_response=False)
        self.assertEqual(fund.funding.get().amount, Decimal("2000"))

    def test_category_budgets_are_dropped_when_a_fund_is_not_a_grant(self):
        BudgetLine.objects.create(worktag=self.gift, category=Category.objects.get(code="travel"), amount=Decimal("500"))
        url = reverse("ledger:fund_edit", args=[self.gift.pk])
        self.client.post(url, {
            "type": "gift", "code": self.gift.code, "name": self.gift.name, "fa_rate": "0", "fa_actuals": "calculate",
            "is_active": "on",
        })
        self.assertFalse(self.gift.budget_lines.exists())

    def test_session_is_refreshed_only_every_few_minutes(self):
        self.client.get(reverse("ledger:dashboard"))
        first = self.client.session["_touched"]
        self.client.get(reverse("ledger:dashboard"))
        self.assertEqual(self.client.session["_touched"], first)
