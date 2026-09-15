from decimal import Decimal

import django.db.models.deletion
from django.db import migrations, models

# The single per-type rates seeded in 0002 are Georgia Tech's FY2025 values.
MIGRATED_FISCAL_YEAR = 2025


def move_rates_to_fiscal_year(apps, schema_editor):
    PersonType = apps.get_model("ledger", "PersonType")
    FiscalYearRate = apps.get_model("ledger", "FiscalYearRate")
    for person_type in PersonType.objects.all():
        FiscalYearRate.objects.create(
            person_type=person_type,
            fiscal_year=MIGRATED_FISCAL_YEAR,
            fringe_rate=person_type.fringe_rate,
            tuition_monthly=person_type.tuition_monthly,
        )


def restore_latest_rates(apps, schema_editor):
    PersonType = apps.get_model("ledger", "PersonType")
    FiscalYearRate = apps.get_model("ledger", "FiscalYearRate")
    for person_type in PersonType.objects.all():
        latest = FiscalYearRate.objects.filter(person_type=person_type).order_by("-fiscal_year").first()
        if latest:
            person_type.fringe_rate = latest.fringe_rate or Decimal("0")
            person_type.tuition_monthly = latest.tuition_monthly or Decimal("0")
            person_type.save()


class Migration(migrations.Migration):
    dependencies = [("ledger", "0002_defaults")]

    operations = [
        migrations.CreateModel(
            name="FiscalYearRate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "fiscal_year",
                    models.PositiveSmallIntegerField(
                        help_text="Year the fiscal year ends: 2027 means July 2026 to June 2027."
                    ),
                ),
                (
                    "fringe_rate",
                    models.DecimalField(
                        blank=True, decimal_places=3, max_digits=6, null=True, verbose_name="Fringe rate (%)"
                    ),
                ),
                (
                    "tuition_monthly",
                    models.DecimalField(
                        blank=True, decimal_places=2, max_digits=10, null=True, verbose_name="Tuition remission / month"
                    ),
                ),
                (
                    "person_type",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="rates", to="ledger.persontype"
                    ),
                ),
            ],
            options={
                "ordering": ["person_type__sort_order", "fiscal_year"],
                "constraints": [
                    models.UniqueConstraint(fields=("person_type", "fiscal_year"), name="one_rate_row_per_fiscal_year")
                ],
            },
        ),
        migrations.RunPython(move_rates_to_fiscal_year, restore_latest_rates),
        migrations.RemoveField(model_name="persontype", name="fringe_rate"),
        migrations.RemoveField(model_name="persontype", name="tuition_monthly"),
        migrations.AlterField(
            model_name="appointment",
            name="fringe_rate_override",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="Leave blank to use the person type's rate for each fiscal year.",
                max_digits=6,
                null=True,
                verbose_name="Fringe rate override (%)",
            ),
        ),
        migrations.AlterField(
            model_name="appointment",
            name="tuition_monthly_override",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Leave blank to use the person type's rate for each fiscal year. Charged in proportion to effort.",
                max_digits=10,
                null=True,
                verbose_name="Tuition / month override",
            ),
        ),
    ]
