import datetime as dt
from decimal import Decimal

from django.test import SimpleTestCase, TestCase, override_settings

from ledger import samples
from ledger.services import fileparse, mapping, pii
from ledger.services.rules import CategoryMatcher, keyword_regex
from ledger.services.values import parse_amount, parse_date, parse_month_header


class ValueParsingTests(SimpleTestCase):
    def test_amounts(self):
        self.assertEqual(parse_amount("$1,234.56"), Decimal("1234.56"))
        self.assertEqual(parse_amount("(12.00)"), Decimal("-12.00"))
        self.assertEqual(parse_amount("12.00-"), Decimal("-12.00"))
        self.assertIsNone(parse_amount(""))
        self.assertIsNone(parse_amount("n/a"))

    def test_dates(self):
        self.assertEqual(parse_date("2026-08-31"), dt.date(2026, 8, 31))
        self.assertEqual(parse_date("08/31/2026"), dt.date(2026, 8, 31))
        self.assertEqual(parse_date("31-AUG-2026"), dt.date(2026, 8, 31))
        self.assertEqual(parse_date("31/08/2026", day_first=True), dt.date(2026, 8, 31))
        self.assertIsNone(parse_date("Total"))

    def test_month_headers(self):
        self.assertEqual(parse_month_header("Jul 2026"), dt.date(2026, 7, 1))
        self.assertEqual(parse_month_header("July-26"), dt.date(2026, 7, 1))
        self.assertEqual(parse_month_header("2026-07"), dt.date(2026, 7, 1))
        self.assertIsNone(parse_month_header("Amount"))


class FileParseTests(SimpleTestCase):
    def test_xlsx_with_title_rows(self):
        sheets = fileparse.read_tables("report.xlsx", samples.workday_expense_xlsx())
        rows = sheets["Find Expense Reports"]
        header = mapping.detect_header_row(rows)
        self.assertEqual(rows[header][0], "Expense Report")
        self.assertEqual(rows[header + 1][2], "2026-08-20")

    def test_peoplesoft_html_disguised_as_xls(self):
        sheets = fileparse.read_tables("ps.xls", samples.oneusg_payroll_html())
        rows = next(iter(sheets.values()))
        self.assertEqual(mapping.detect_header_row(rows), 1)
        self.assertEqual(rows[2][2], "Doe,Jane A")

    def test_csv_and_rejects_empty(self):
        sheets = fileparse.read_tables("a.csv", b"Date,Amount\n2026-01-05,10.00\n")
        self.assertEqual(next(iter(sheets.values())), [["Date", "Amount"], ["2026-01-05", "10.00"]])
        with self.assertRaises(fileparse.FileParseError):
            fileparse.read_tables("a.csv", b"   ")


@override_settings(PII_HMAC_KEY="test-key")
class PiiTests(SimpleTestCase):
    def test_classify_headers(self):
        self.assertEqual(pii.classify_header("Worker"), "name")
        self.assertEqual(pii.classify_header("Emp ID"), "id")
        self.assertIsNone(pii.classify_header("Cost Center Name"))
        self.assertIsNone(pii.classify_header("Payee Type"))

    def test_name_formats_share_a_key(self):
        _, a = pii.pseudonymize("Jane Doe", "name")
        _, b = pii.pseudonymize("Doe,Jane A", "name")
        self.assertEqual(a["key"], b["key"])
        self.assertEqual(a["initials"], "JD")

    def test_scrub_masks_names_everywhere_in_row(self):
        rows = [["Report", "Worker", "SSN note"], ["EXP-1 - Jane Doe on 08/20", "Jane Doe", "id 123-45-6789"]]
        scrubbed, tokens, columns = pii.scrub_rows(rows, 0)
        text = repr(scrubbed)
        self.assertNotIn("Jane", text)
        self.assertNotIn("123-45-6789", text)
        self.assertEqual(columns, [1])
        self.assertEqual(len(tokens), 1)
        self.assertIn("JD", scrubbed[1][0])


class MappingTests(SimpleTestCase):
    def test_auto_map_workday_headers(self):
        headers = ["Expense Report", "Worker", "Expense Report Date", "Expense Item", "Memo", "Worktags",
                   "Total Amount", "Expense Report Status"]
        result = mapping.auto_map(headers, "workday_expense")
        self.assertEqual(result["date"], "Expense Report Date")
        self.assertEqual(result["amount"], ["Total Amount"])
        self.assertEqual(result["person"], "Worker")
        self.assertEqual(result["status"], "Expense Report Status")
        self.assertEqual(result["reference"], "Expense Report")
        self.assertEqual(result["worktags"], ["Worktags"])

    def test_auto_map_oneusg_headers(self):
        headers = ["Pay Run ID", "Emp ID", "Name", "Acct", "Descr", "Project", "Expended Date", "Amount"]
        result = mapping.auto_map(headers, "oneusg_payroll")
        self.assertEqual(result["date"], "Expended Date")
        self.assertEqual(result["person"], "Name")
        self.assertEqual(result["category_raw"], "Acct")
        self.assertIn("Project", result["worktags"])


class RuleTests(TestCase):
    def test_keyword_regex_whole_words_and_prefix(self):
        regex = keyword_regex("meal, 5161*")
        self.assertTrue(regex.search("Meal - visitor"))
        self.assertFalse(regex.search("oatmeal"))
        self.assertTrue(regex.search("acct 516100"))

    def test_default_rules_categorize(self):
        from ledger.models import CategoryRule

        matcher = CategoryMatcher(CategoryRule.objects.select_related("category"))
        self.assertEqual(matcher.match("Airfare").code, "travel")
        self.assertEqual(matcher.match("Fringe Benefits").code, "fringe")
        self.assertEqual(matcher.match("Salaries - Postdoc").code, "salaries")
        self.assertIsNone(matcher.match("Mystery"))
