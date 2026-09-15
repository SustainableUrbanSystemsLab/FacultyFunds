import datetime as dt
import json
from decimal import Decimal

from django.test import TestCase

from ledger import samples
from ledger.models import (
    Appointment,
    BudgetLine,
    Category,
    ImportBatch,
    ImportProfile,
    PendingEntry,
    Person,
    PersonType,
    Transaction,
    Worktag,
)
from ledger.services import importer
from ledger.services.projections import summarize_fund


class ImportPipelineTests(TestCase):
    def setUp(self):
        self.grant = Worktag.objects.create(code="GR00012345", name="Solar", fa_rate=Decimal("50"))

    def stage_workday(self):
        return importer.stage_upload("expenses.xlsx", samples.workday_expense_xlsx(), "workday_expense")

    def test_workday_import_end_to_end(self):
        batch = self.stage_workday()
        self.assertNotIn("Jane", json.dumps(batch.sheets))
        records, summary = importer.parse_batch(batch)
        self.assertEqual(summary["importable"], 4)  # draft row and total row skipped
        self.assertEqual(summary["skipped"], {"status": 1, "no readable date": 1})
        self.assertEqual(summary["no_fund"], 1)  # gift GF00077777 isn't tracked
        self.assertIn("GF00077777", dict(summary["unknown_codes"]))

        stats = importer.commit_batch(batch, save_profile=True, profile_name="Workday expenses")
        self.assertEqual(stats["created"], 4)
        self.assertEqual(Transaction.objects.filter(worktag=self.grant).count(), 3)
        self.assertEqual(Transaction.objects.get(description="Hotel, 3 nights").category.code, "travel")
        self.assertEqual(Person.objects.count(), 2)
        batch.refresh_from_db()
        self.assertEqual(batch.sheets, {})

        # Same file again: profile applied automatically, everything is a duplicate.
        again = self.stage_workday()
        self.assertEqual(again.profile, ImportProfile.objects.get())
        _, summary = importer.parse_batch(again)
        self.assertEqual((summary["importable"], summary["duplicates"]), (0, 4))

    def test_payroll_links_people_across_sources_and_undo(self):
        importer.commit_batch(self.stage_workday())
        batch = importer.stage_upload("ps.xls", samples.oneusg_payroll_html(), "oneusg_payroll")
        stats = importer.commit_batch(batch)
        self.assertEqual(stats["created"], 4)
        # "Jane Doe" (Workday) and "Doe,Jane A" (OneUSG) are the same person.
        self.assertEqual(Person.objects.filter(alias__startswith="JD").count(), 1)
        self.assertEqual(Transaction.objects.get(amount=Decimal("1585.00")).category.code, "fringe")

        removed, _ = importer.undo_batch(ImportBatch.objects.get(pk=batch.pk))
        self.assertEqual(removed, 4)
        self.assertEqual(Transaction.objects.filter(source="oneusg_payroll").count(), 0)

    def test_import_as_pending(self):
        batch = self.stage_workday()
        batch.options["import_as"] = "pending"
        stats = importer.commit_batch(batch)
        self.assertEqual(stats["created"], 3)
        self.assertEqual(stats["skipped_no_fund"], 1)
        self.assertEqual(PendingEntry.objects.count(), 3)

    def test_rematch_after_adding_fund(self):
        importer.commit_batch(self.stage_workday())
        Worktag.objects.create(code="GF00077777", name="Gift", type="gift")
        funds, _ = importer.rematch_transactions()
        self.assertEqual(funds, 1)
        self.assertFalse(Transaction.objects.filter(worktag__isnull=True).exists())


class ProjectionTests(TestCase):
    today = dt.date(2026, 9, 14)

    def setUp(self):
        cat = {c.code: c for c in Category.objects.all()}
        self.cat = cat
        self.fund = Worktag.objects.create(
            code="GR1", name="Grant", fa_rate=Decimal("50"), start_date=dt.date(2026, 1, 1), end_date=dt.date(2027, 6, 30)
        )
        for code, amount in {"salaries": 60000, "fringe": 5000, "travel": 5000, "supplies": 2000, "fa": 20000}.items():
            BudgetLine.objects.create(worktag=self.fund, category=cat[code], amount=amount)
        gra = PersonType.objects.create(name="Test GRA", fringe_rate=Decimal("7.9"), tuition_monthly=Decimal("1596"))
        self.person = Person.objects.create(alias="JD", person_type=gra)
        Appointment.objects.create(
            person=self.person, worktag=self.fund, start_date=dt.date(2026, 8, 1), end_date=dt.date(2026, 12, 31),
            monthly_salary=Decimal("2500"),
        )
        Transaction.objects.create(worktag=self.fund, category=cat["salaries"], person=self.person,
                                   date=dt.date(2026, 8, 31), amount=Decimal("2500"))
        Transaction.objects.create(worktag=self.fund, category=cat["travel"], date=dt.date(2026, 8, 20), amount=Decimal("1000"))

    def line(self, summary, code):
        return next(line for line in summary.lines if line.category.code == code)

    def test_actuals_plans_fa_and_burn(self):
        s = summarize_fund(self.fund, today=self.today)
        self.assertEqual(self.line(s, "fringe").actual, Decimal("197.50"))  # estimated from 7.9%
        self.assertEqual(self.line(s, "salaries").planned, Decimal("10000.00"))  # Sep–Dec; Aug already paid
        self.assertEqual(self.line(s, "fringe").planned, Decimal("790.00"))
        self.assertEqual(self.line(s, "tuition").planned, Decimal("7980.00"))  # Aug–Dec, no tuition actuals
        self.assertEqual(self.line(s, "fa").actual, Decimal("1848.75"))  # 50% of 3,697.50 MTDC
        self.assertEqual(self.line(s, "fa").planned, Decimal("5395.00"))
        self.assertEqual(s.total.budget, Decimal("92000"))
        self.assertEqual(s.total.projected_remaining, Decimal("62288.75"))
        self.assertEqual(s.burn_rate, Decimal("924.38"))  # Aug spending / 6 complete months
        self.assertFalse(any(a.level == "danger" for a in s.alerts))

    def test_pending_counts_and_overspend_alert(self):
        PendingEntry.objects.create(worktag=self.fund, category=self.cat["travel"], description="Trip",
                                    amount=Decimal("4500"), expected_date=dt.date(2026, 10, 1))
        s = summarize_fund(self.fund, today=self.today)
        travel = self.line(s, "travel")
        self.assertEqual(travel.pending, Decimal("4500.00"))
        self.assertEqual(self.line(s, "fa").pending, Decimal("2250.00"))
        self.assertTrue(any("Travel projected over" in a.message for a in s.alerts))

    def test_imported_fa_ignored_when_calculating(self):
        Transaction.objects.create(worktag=self.fund, category=self.cat["fa"], date=dt.date(2026, 8, 31), amount=Decimal("999"))
        s = summarize_fund(self.fund, today=self.today)
        self.assertEqual(self.line(s, "fa").actual, Decimal("1848.75"))
        self.assertTrue(any("ignored" in a.message for a in s.alerts))
