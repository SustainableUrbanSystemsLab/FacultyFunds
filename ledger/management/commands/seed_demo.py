import datetime as dt
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ledger import samples
from ledger.models import Appointment, BudgetLine, Category, Funding, Person, PersonType, Worktag, WorktagType


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
                "type": WorktagType.GRANT, "name": "Solar Facade Study (demo)", "sponsor": "Example Agency",
                "start_date": dt.date(2026, 1, 1), "end_date": dt.date(2027, 12, 31), "fa_rate": Decimal("57.4"),
                "match_codes": "AWD000123",
            },
        )
        if created:
            budget = {"salaries": 90000, "fringe": 12000, "tuition": 40000, "travel": 8000, "supplies": 6000, "fa": 60000}
            for code, amount in budget.items():
                BudgetLine.objects.create(worktag=grant, category=Category.objects.get(code=code), amount=amount)
            Funding.objects.create(worktag=grant, date=dt.date(2026, 1, 1), amount=sum(budget.values()), description="Award")
        fellowship, created = Worktag.objects.get_or_create(
            code="DE00099999",
            defaults={"type": WorktagType.GIFT, "name": "Faculty Fellow award (demo)"},
        )
        if created:
            Funding.objects.create(worktag=fellowship, date=dt.date(2026, 9, 1), amount=2000, description="Award transferred")
        state, created = Worktag.objects.get_or_create(
            code="CC0001234", defaults={"type": WorktagType.STATE, "name": "Department funds (demo)"},
        )
        if created:
            Funding.objects.create(worktag=state, date=dt.date.today(), amount=15000, description="Allocation for this fiscal year")

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
