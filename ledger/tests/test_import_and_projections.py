import datetime as dt
import json
from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from ledger import samples
from ledger.models import (
    Appointment,
    BudgetLine,
    Category,
    FiscalYearRate,
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
from ledger.services.rates import RateTable, fiscal_year


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
        self.assertEqual(summary["skipped"], {"status": 1, "total row": 1})
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
        batch.save()
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

    def test_cancelled_report_removes_earlier_import(self):
        importer.commit_batch(self.stage_workday())
        self.assertEqual(Transaction.objects.filter(amount=Decimal("120.00")).count(), 1)
        # Next month's export: the $120 supplies report was cancelled, everything else unchanged.
        rows = [row[:7] + ("Canceled",) if row[6] == 120.00 else row for row in samples.WORKDAY_ROWS]
        batch = importer.stage_upload("expenses-2.xlsx", samples.workday_expense_xlsx(rows), "workday_expense")
        records, summary = importer.parse_batch(batch)
        self.assertEqual((summary["importable"], summary["duplicates"], summary["stale"]), (0, 3, 1))
        stale = next(r for r in records if r.stale_existing)
        self.assertTrue(stale.skip_reason.startswith("status:"))
        stats = importer.commit_batch(batch)
        self.assertEqual((stats["created"], stats["removed"]), (0, 1))
        self.assertFalse(Transaction.objects.filter(amount=Decimal("120.00")).exists())
        self.assertEqual(Transaction.objects.count(), 3)

    def test_total_row_skipped_in_wide_worksheet(self):
        csv = (
            b"Name,Project,Jul 2026,Aug 2026\n"
            b"Doe Jane,GR00012345,2500,2500\n"
            b"Total,,2500,2500\n"
        )
        batch = importer.stage_upload("plan.csv", csv, "other_import")
        batch.column_map = {"amount": ["Jul 2026", "Aug 2026"], "worktags": ["Project"], "person": "Name", "date": ""}
        records, summary = importer.parse_batch(batch)
        self.assertEqual(summary["importable"], 2)
        self.assertEqual(summary["skipped"], {"total row": 2})
        self.assertEqual({r.date for r in records if r.importable}, {dt.date(2026, 7, 1), dt.date(2026, 8, 1)})

    def test_commit_twice_is_rejected(self):
        batch = self.stage_workday()
        importer.commit_batch(batch)
        with self.assertRaises(importer.BatchNotStaged):
            importer.commit_batch(ImportBatch.objects.get(pk=batch.pk))
        self.assertEqual(Transaction.objects.count(), 4)

    def test_unrecognized_person_column_is_scrubbed_when_mapped(self):
        csv = b"Date,Beneficiary,Amount,Grant\n2026-08-01,Jane Doe,10.00,GR00012345\n"
        batch = importer.stage_upload("x.csv", csv, "other_import")
        self.assertIn("Jane Doe", json.dumps(batch.sheets))  # "Beneficiary" isn't a known people header
        batch.column_map["person"] = "Beneficiary"
        self.assertEqual(importer.ensure_person_column_scrubbed(batch), "Beneficiary")
        self.assertNotIn("Jane", json.dumps(batch.sheets))
        self.assertEqual(len(batch.person_tokens), 1)


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
        gra = PersonType.objects.create(name="Test GRA")
        FiscalYearRate.objects.create(
            person_type=gra, fiscal_year=2027, fringe_rate=Decimal("7.9"), tuition_monthly=Decimal("1596")
        )
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
        self.assertEqual(s.burn_rate, Decimal("5546.25"))  # spending began in Aug: averaged over Aug only
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

    def test_rates_follow_fiscal_year_and_reuse_latest(self):
        FiscalYearRate.objects.create(
            person_type=self.person.person_type, fiscal_year=2028, fringe_rate=Decimal("10"), tuition_monthly=Decimal("2000")
        )
        fund = Worktag.objects.create(code="GR2", name="Two", start_date=dt.date(2027, 1, 1), end_date=dt.date(2029, 6, 30))
        # June 2027 is FY2027, July 2027 is FY2028, July 2028 is FY2029 (not entered: reuses FY2028).
        for start, end in [(dt.date(2027, 6, 1), dt.date(2027, 6, 30)), (dt.date(2027, 7, 1), dt.date(2027, 7, 31)),
                           (dt.date(2028, 7, 1), dt.date(2028, 7, 31))]:
            Appointment.objects.create(person=self.person, worktag=fund, start_date=start, end_date=end,
                                       monthly_salary=Decimal("1000"))
        s = summarize_fund(fund, today=self.today)
        self.assertEqual(self.line(s, "salaries").planned, Decimal("3000.00"))
        self.assertEqual(self.line(s, "fringe").planned, Decimal("279.00"))  # 79 + 100 + 100
        self.assertEqual(self.line(s, "tuition").planned, Decimal("5596.00"))  # 1596 + 2000 + 2000
        self.assertTrue(any("FY2029" in a.message and "FY2028" in a.message for a in s.alerts))

    def test_unlinked_fringe_row_suppresses_estimate(self):
        # A fringe line with no person in August covers everyone's fringe that month.
        Transaction.objects.create(worktag=self.fund, category=self.cat["fringe"], date=dt.date(2026, 8, 31), amount=Decimal("300"))
        s = summarize_fund(self.fund, today=self.today)
        self.assertEqual(self.line(s, "fringe").actual, Decimal("300.00"))
        self.assertEqual(self.line(s, "fringe").estimated, Decimal("0.00"))

    def test_runway_and_burn_with_tiny_spending(self):
        fund = Worktag.objects.create(code="GR9", name="Huge", start_date=dt.date(2020, 1, 1))
        BudgetLine.objects.create(worktag=fund, category=self.cat["other"], amount=Decimal("900000000"))
        Transaction.objects.create(worktag=fund, category=self.cat["other"], date=dt.date(2026, 7, 3), amount=Decimal("0.05"))
        s = summarize_fund(fund, today=self.today)  # must not overflow date arithmetic
        self.assertEqual(s.burn_rate, Decimal("0.03"))  # averaged from first spending month (Jul, Aug), not six
        self.assertIsNone(s.run_out_date)
        self.assertGreater(s.runway_months, 1200)

    def test_past_planned_months_alert(self):
        fund = Worktag.objects.create(code="GR3", name="Three")
        Appointment.objects.create(person=self.person, worktag=fund, start_date=dt.date(2026, 5, 1),
                                   end_date=dt.date(2026, 12, 31), monthly_salary=Decimal("1000"))
        s = summarize_fund(fund, today=self.today)
        self.assertTrue(any("4 past months (May 2026 – Aug 2026)" in a.message for a in s.alerts))

    def test_estimated_fringe_uses_fiscal_year_of_pay_month(self):
        FiscalYearRate.objects.create(person_type=self.person.person_type, fiscal_year=2026, fringe_rate=Decimal("5"))
        Transaction.objects.create(worktag=self.fund, category=self.cat["salaries"], person=self.person,
                                   date=dt.date(2026, 6, 30), amount=Decimal("1000"))
        s = summarize_fund(self.fund, today=self.today)
        # June 2026 at FY2026's 5% plus August 2026 at FY2027's 7.9%.
        self.assertEqual(self.line(s, "fringe").actual, Decimal("247.50"))


class RateTableTests(SimpleTestCase):
    def test_fiscal_year_boundaries(self):
        self.assertEqual(fiscal_year(dt.date(2026, 6, 30)), 2026)
        self.assertEqual(fiscal_year(dt.date(2026, 7, 1)), 2027)

    def test_lookup_reuses_closest_earlier_year(self):
        table = RateTable([
            FiscalYearRate(person_type_id=1, fiscal_year=2025, fringe_rate=Decimal("31.7"), tuition_monthly=None),
            FiscalYearRate(person_type_id=1, fiscal_year=2027, fringe_rate=Decimal("33"), tuition_monthly=Decimal("0")),
        ])
        self.assertEqual(table.lookup(1, "fringe_rate", 2026), (Decimal("31.7"), 2025))
        self.assertEqual(table.lookup(1, "fringe_rate", 2030), (Decimal("33"), 2027))
        self.assertEqual(table.lookup(1, "fringe_rate", 2020), (Decimal("31.7"), 2025))
        self.assertEqual(table.lookup(1, "tuition_monthly", 2026), (Decimal("0"), 2027))
        self.assertEqual(table.lookup(2, "fringe_rate", 2027), (Decimal("0"), None))
