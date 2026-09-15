import datetime as dt
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ledger import samples
from ledger.models import Appointment, BudgetLine, Category, Person, PersonType, Worktag, WorktagType


class Command(BaseCommand):
    help = "Add example funds, budgets, and appointments, and write sample export files to try the importer."

    def add_arguments(self, parser):
        parser.add_argument("--files", default="samples/demo", help="Folder for sample export files.")
        parser.add_argument("--force", action="store_true", help="Allow when DEBUG is off.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError("Demo data is meant for local testing. Use --force to add it anyway.")

        grant, created = Worktag.objects.get_or_create(
            code="GR00012345",
            defaults={
                "type": WorktagType.GRANT, "name": "Solar Facade Study (demo)", "sponsor": "Example Foundation",
                "start_date": dt.date(2026, 1, 1), "end_date": dt.date(2027, 12, 31), "fa_rate": Decimal("57.4"),
                "match_codes": "AWD000123",
            },
        )
        if created:
            budget = {"salaries": 90000, "fringe": 12000, "tuition": 40000, "travel": 8000, "supplies": 6000, "fa": 60000}
            for code, amount in budget.items():
                BudgetLine.objects.create(worktag=grant, category=Category.objects.get(code=code), amount=amount)
        startup, created = Worktag.objects.get_or_create(
            code="CC0001234",
            defaults={"type": WorktagType.COST_CENTER, "name": "Startup package (demo)", "end_date": dt.date(2028, 6, 30)},
        )
        if created:
            BudgetLine.objects.create(worktag=startup, category=Category.objects.get(code="other"), amount=150000)
            BudgetLine.objects.create(worktag=startup, category=Category.objects.get(code="salaries"), amount=20000)

        gra = PersonType.objects.filter(name__icontains="graduate").first()
        student = Person.objects.filter(alias="JD").first() or Person.objects.create(alias="Demo GRA", person_type=gra)
        if not Appointment.objects.filter(worktag=grant).exists():
            Appointment.objects.create(
                person=student, worktag=grant, start_date=dt.date(2026, 8, 16), end_date=dt.date(2027, 5, 15),
                monthly_salary=Decimal("2500"), effort_pct=Decimal("100"),
            )

        folder = Path(options["files"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "workday-expense-reports.xlsx").write_bytes(samples.workday_expense_xlsx())
        (folder / "oneusg-payroll-expenses-detail.xls").write_bytes(samples.oneusg_payroll_html())
        self.stdout.write(self.style.SUCCESS(f"Demo funds ready. Sample files written to {folder}/"))
