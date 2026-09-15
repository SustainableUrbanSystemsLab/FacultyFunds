from decimal import Decimal

from django.db import migrations

CATEGORIES = [
    # code, name, subject to F&A (MTDC), sort order
    ("salaries", "Salaries & wages", True, 10),
    ("fringe", "Fringe benefits", True, 20),
    ("tuition", "Tuition remission", False, 30),
    ("travel", "Travel", True, 40),
    ("equipment", "Equipment", False, 50),
    ("supplies", "Materials & supplies", True, 60),
    ("participant", "Participant support", False, 70),
    ("other", "Other direct costs", True, 80),
    ("fa", "F&A (indirect costs)", False, 90),
]

# Starting values from Georgia Tech's FY2025 rate schedules (fringe) and GSTRP (graduate
# tuition remission per month). Verify current rates and edit them under Settings.
PERSON_TYPES = [
    ("Faculty (full benefits)", "31.7", "0", 10),
    ("Postdoc (full benefits)", "31.7", "0", 20),
    ("Staff (full benefits)", "31.7", "0", 30),
    ("Limited benefits", "23.9", "0", 40),
    ("Graduate research assistant", "7.9", "1596", 50),
    ("Student hourly / partial benefits", "1.5", "0", 60),
]

RULES = [
    ("fringe, fringe benefits, benefit, benefits, employer contribution, employer contributions, retirement, "
     "fica, medicare, health insurance", "fringe", 10),
    ("tuition, tuition remission, gstrp, fee remission, matriculation", "tuition", 20),
    ("f&a, indirect, indirect costs, facilities and administrative, overhead", "fa", 30),
    ("participant support, participant stipend, participant", "participant", 35),
    ("salary, salaries, wages, regular pay, earnings, summer pay, stipend, graduate assistant, hourly, overtime",
     "salaries", 40),
    ("travel, airfare, air fare, lodging, hotel, meals, meal, per diem, mileage, rental car, car rental, taxi, "
     "rideshare, uber, lyft, parking, baggage, conference registration, registration fee, train, tolls", "travel", 50),
    ("equipment, capital equipment", "equipment", 60),
    ("supplies, supply, materials, lab supplies, computer, computers, software, books, office supplies, chemicals",
     "supplies", 70),
    ("publication, page charges, printing, shipping, postage, membership, dues, subscription, services, "
     "consultant, consulting, repair, repairs, maintenance, rental, telephone, internet, fees", "other", 80),
]


def create_defaults(apps, schema_editor):
    Category = apps.get_model("ledger", "Category")
    PersonType = apps.get_model("ledger", "PersonType")
    CategoryRule = apps.get_model("ledger", "CategoryRule")

    by_code = {}
    for code, name, fa_eligible, order in CATEGORIES:
        by_code[code], _ = Category.objects.get_or_create(
            code=code, defaults={"name": name, "fa_eligible": fa_eligible, "sort_order": order}
        )
    for name, fringe, tuition, order in PERSON_TYPES:
        PersonType.objects.get_or_create(
            name=name,
            defaults={"fringe_rate": Decimal(fringe), "tuition_monthly": Decimal(tuition), "sort_order": order},
        )
    if not CategoryRule.objects.exists():
        for keywords, code, priority in RULES:
            CategoryRule.objects.create(keywords=keywords, category=by_code[code], priority=priority)


class Migration(migrations.Migration):
    dependencies = [("ledger", "0001_initial")]
    operations = [migrations.RunPython(create_defaults, migrations.RunPython.noop)]
