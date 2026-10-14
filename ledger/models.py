from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.urls import reverse
from django.utils import timezone

from .fiscal import fiscal_year, fiscal_year_bounds

ZERO = Decimal("0")
WEEKS_PER_MONTH = Decimal("52") / 12


class WorktagType(models.TextChoices):
    GRANT = "grant", "Federal grant"
    GIFT = "gift", "Foundation / gift"
    STATE = "state", "State funds"


# When one transaction carries several tracked worktags, the most specific fund wins.
WORKTAG_PRIORITY = [WorktagType.GRANT, WorktagType.GIFT, WorktagType.STATE]


class Source(models.TextChoices):
    WORKDAY_EXPENSE = "workday_expense", "Workday expense report"
    ONEUSG_PAYROLL = "oneusg_payroll", "OneUSG payroll worksheet"
    OTHER_IMPORT = "other_import", "Other spreadsheet"
    MANUAL = "manual", "Manual entry"


IMPORT_SOURCE_CHOICES = [choice for choice in Source.choices if choice[0] != Source.MANUAL]


class Worktag(models.Model):
    """A fund you track: a federal grant, a foundation or gift fund, or state funds."""

    class FAActuals(models.TextChoices):
        CALCULATE = "calculate", "Calculate from rate"
        IMPORTED = "imported", "Use imported F&A lines"

    type = models.CharField(max_length=20, choices=WorktagType.choices, default=WorktagType.GRANT)
    code = models.CharField(
        "Worktag ID", max_length=64, unique=True, null=True, blank=True,
        help_text="Optional until Workday assigns one. Add it later by editing the fund.",
    )
    name = models.CharField(max_length=200)
    match_codes = models.TextField(
        "Other IDs for this fund",
        blank=True,
        help_text="One per line: award ID (AWD…), OneUSG project or combo code, etc. Used to match imported rows.",
    )
    sponsor = models.CharField(max_length=200, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    fa_rate = models.DecimalField(
        "F&A rate (%)",
        max_digits=6,
        decimal_places=3,
        default=ZERO,
        help_text="Indirect cost rate on modified total direct costs (MTDC). 0 if none.",
    )
    fa_actuals = models.CharField(
        "F&A on actual spending", max_length=12, choices=FAActuals.choices, default=FAActuals.CALCULATE
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_active", "end_date", "code"]
        verbose_name = "fund"

    def __str__(self):
        return f"{self.code} · {self.name}" if self.code else self.name

    @property
    def display_code(self):
        return self.code or self.name

    def get_absolute_url(self):
        return reverse("ledger:fund_detail", args=[self.pk])

    @property
    def all_codes(self) -> list[str]:
        codes = [self.code, *(line.strip() for line in self.match_codes.splitlines())]
        return [code for code in codes if code]

    @property
    def priority(self) -> int:
        return WORKTAG_PRIORITY.index(self.type)

    @property
    def lapses_yearly(self) -> bool:
        """State money expires on June 30; it is tracked one fiscal year at a time."""
        return self.type == WorktagType.STATE

    def period(self, today) -> tuple:
        """Current state fiscal year, capped at a chosen expiration date; retain the final year after expiry."""
        if not self.lapses_yearly:
            return None, None
        tracked_day = min(today, self.end_date) if self.end_date else today
        start, end = fiscal_year_bounds(fiscal_year(tracked_day))
        return start, min(end, self.end_date) if self.end_date else end


class Funding(models.Model):
    """Money added to a fund: the award, a transfer or top-up, or a state fund's allocation for a fiscal year."""

    worktag = models.ForeignKey(Worktag, related_name="funding", on_delete=models.CASCADE, verbose_name="fund")
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "funding"

    def __str__(self):
        return f"{self.worktag.display_code} +{self.amount} ({self.date})"

    @property
    def fiscal_year(self) -> int:
        return fiscal_year(self.date)


class Category(models.Model):
    SALARIES = "salaries"
    FRINGE = "fringe"
    TUITION = "tuition"
    FA = "fa"
    OTHER = "other"
    SYSTEM_CODES = {SALARIES, FRINGE, TUITION, FA, OTHER}

    code = models.SlugField(max_length=40, unique=True)
    name = models.CharField(max_length=80)
    fa_eligible = models.BooleanField(
        "Subject to F&A", default=True, help_text="Included in the MTDC base that F&A is charged on."
    )
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name

    @property
    def is_system(self) -> bool:
        return self.code in self.SYSTEM_CODES

    def save(self, *args, **kwargs):
        if self.code == self.FA:
            self.fa_eligible = False
        super().save(*args, **kwargs)


class BudgetLine(models.Model):
    worktag = models.ForeignKey(Worktag, related_name="budget_lines", on_delete=models.CASCADE)
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["category__sort_order"]
        constraints = [models.UniqueConstraint(fields=["worktag", "category"], name="one_budget_line_per_category")]

    def __str__(self):
        return f"{self.worktag.display_code} {self.category}: {self.amount}"


class PersonType(models.Model):
    name = models.CharField(max_length=80, unique=True)
    sort_order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class FiscalYearRate(models.Model):
    """Fringe rate and monthly tuition remission for one person type in one fiscal year (July–June)."""

    person_type = models.ForeignKey(PersonType, related_name="rates", on_delete=models.CASCADE)
    fiscal_year = models.PositiveSmallIntegerField(
        help_text="Year the fiscal year ends: 2027 means July 2026 to June 2027."
    )
    fringe_rate = models.DecimalField("Fringe rate (%)", max_digits=6, decimal_places=3, null=True, blank=True)
    tuition_monthly = models.DecimalField(
        "Tuition remission / month", max_digits=10, decimal_places=2, null=True, blank=True
    )

    class Meta:
        ordering = ["person_type__sort_order", "fiscal_year"]
        constraints = [
            models.UniqueConstraint(fields=["person_type", "fiscal_year"], name="one_rate_row_per_fiscal_year")
        ]

    def __str__(self):
        return f"{self.person_type} FY{self.fiscal_year}"


class Person(models.Model):
    alias = models.CharField(max_length=80, unique=True, help_text="Initials or a nickname. Real names are never stored.")
    person_type = models.ForeignKey(PersonType, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField("Active", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_active", "alias"]
        verbose_name_plural = "people"

    def __str__(self):
        return self.alias

    def get_absolute_url(self):
        return reverse("ledger:person_detail", args=[self.pk])


class PersonKey(models.Model):
    """Keyed hash (HMAC) of a name or employee ID seen in an import.

    Lets later imports recognize the same person without storing who they are.
    """

    class Kind(models.TextChoices):
        NAME = "name", "Name"
        EMPLOYEE_ID = "id", "Employee ID"

    person = models.ForeignKey(Person, related_name="keys", on_delete=models.CASCADE)
    key_hash = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices)
    created_at = models.DateTimeField(auto_now_add=True)


class Appointment(models.Model):
    """Planned payroll on a fund, used for projections."""

    class Kind(models.TextChoices):
        REGULAR = "regular", "Monthly salary"
        SUMMER = "summer", "Summer salary"
        HOURLY = "hourly", "Hourly (student worker)"

    person = models.ForeignKey(Person, related_name="appointments", on_delete=models.CASCADE)
    worktag = models.ForeignKey(Worktag, related_name="appointments", on_delete=models.CASCADE, verbose_name="fund")
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.REGULAR)
    start_date = models.DateField()
    end_date = models.DateField()
    monthly_salary = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=ZERO,
        blank=True,
        validators=[MinValueValidator(ZERO)],
        help_text="Full monthly salary at 100% effort.",
    )
    hourly_rate = models.DecimalField(
        "Hourly rate",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO)],
    )
    hours_per_week = models.DecimalField(
        "Hours per week",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("168"))],
    )
    effort_pct = models.DecimalField(
        "Effort on this fund (%)",
        max_digits=6,
        decimal_places=2,
        default=Decimal("100"),
        validators=[MinValueValidator(ZERO), MaxValueValidator(Decimal("100"))],
    )
    fringe_rate_override = models.DecimalField(
        "Fringe rate override (%)",
        max_digits=6,
        decimal_places=3,
        null=True,
        blank=True,
        help_text="Leave blank to use the person type's rate for each fiscal year.",
    )
    tuition_monthly_override = models.DecimalField(
        "Tuition / month override",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Leave blank to use the person type's rate for each fiscal year. Charged in proportion to effort.",
    )
    notes = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["start_date", "id"]

    def __str__(self):
        return f"{self.person} on {self.worktag.display_code} ({self.start_date:%b %Y}–{self.end_date:%b %Y})"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date must be on or after the start date."})
        if self.kind == self.Kind.HOURLY:
            errors = {}
            if self.hourly_rate is None:
                errors["hourly_rate"] = "Enter the hourly rate."
            if self.hours_per_week is None:
                errors["hours_per_week"] = "Enter the hours per week."
            if errors:
                raise ValidationError(errors)

    @property
    def is_hourly(self) -> bool:
        return self.kind == self.Kind.HOURLY

    @property
    def monthly_charge(self) -> Decimal:
        """Salary charged to the fund each month (hourly work is rate x hours x 52 weeks / 12 months)."""
        if self.is_hourly:
            return (self.hourly_rate or ZERO) * (self.hours_per_week or ZERO) * WEEKS_PER_MONTH
        return self.monthly_salary * self.effort_pct / 100


class CategoryRule(models.Model):
    keywords = models.CharField(
        max_length=400,
        help_text="Comma-separated words or phrases, case-insensitive. End with * to match the start of a word (e.g. 5161*).",
    )
    category = models.ForeignKey(Category, related_name="rules", on_delete=models.CASCADE)
    priority = models.PositiveIntegerField(default=100, help_text="Lower numbers are checked first.")

    class Meta:
        ordering = ["priority", "id"]

    def __str__(self):
        return f"{self.keywords} → {self.category}"


class ImportProfile(models.Model):
    """Saved column mapping, reused automatically when a file with the same headers is uploaded."""

    name = models.CharField(max_length=120)
    source = models.CharField(max_length=20, choices=IMPORT_SOURCE_CHOICES)
    header_signature = models.CharField(max_length=64, db_index=True)
    column_map = models.JSONField(default=dict)
    options = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_used_at", "name"]

    def __str__(self):
        return self.name


class ImportBatch(models.Model):
    class Status(models.TextChoices):
        STAGED = "staged", "Waiting for review"
        COMMITTED = "committed", "Imported"
        CANCELLED = "cancelled", "Cancelled"
        UNDONE = "undone", "Undone"

    source = models.CharField(max_length=20, choices=IMPORT_SOURCE_CHOICES)
    filename = models.CharField(max_length=255)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.STAGED, db_index=True)
    profile = models.ForeignKey(ImportProfile, null=True, blank=True, on_delete=models.SET_NULL)
    sheet_name = models.CharField(max_length=120, blank=True)
    # Scrubbed (pseudonymized) cell text per sheet, kept only while staged.
    sheets = models.JSONField(default=dict)
    header_row = models.PositiveIntegerField(default=0)
    column_map = models.JSONField(default=dict)
    options = models.JSONField(default=dict)
    # token -> {"key": hmac, "kind": "name"|"id", "initials": str}; kept only while staged.
    person_tokens = models.JSONField(default=dict)
    stats = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "import batches"

    def __str__(self):
        return f"{self.filename} ({self.get_status_display()})"

    @property
    def sheet_names(self) -> list[str]:
        return list(self.sheets.keys())

    @property
    def rows(self) -> list[list[str]]:
        return self.sheets.get(self.sheet_name, [])


class Transaction(models.Model):
    """Actual spending (positive) or credits (negative) on a fund."""

    worktag = models.ForeignKey(
        Worktag, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions", verbose_name="fund"
    )
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.PROTECT)
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL)
    date = models.DateField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    description = models.CharField(max_length=500, blank=True)
    reference = models.CharField(max_length=120, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.MANUAL)
    raw_category = models.CharField("Category in file", max_length=200, blank=True)
    raw_worktags = models.TextField("Worktags in file", blank=True)
    raw_status = models.CharField("Status in file", max_length=60, blank=True)
    is_estimate = models.BooleanField(default=False, help_text="Calculated rather than reported (e.g., fringe from rate).")
    # SET_NULL: removing an old import record must never delete the spending it brought in (use Undo for that).
    batch = models.ForeignKey(
        ImportBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="transactions"
    )
    dedupe_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["worktag", "category", "date"])]

    def __str__(self):
        return f"{self.date} {self.amount} {self.description[:40]}"


class PendingEntry(models.Model):
    """Spending you expect but that hasn't shown up in a report yet (orders, planned travel...)."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RECONCILED = "reconciled", "Reconciled"
        CANCELLED = "cancelled", "Cancelled"

    worktag = models.ForeignKey(
        Worktag, on_delete=models.CASCADE, related_name="pending_entries", verbose_name="fund"
    )
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL)
    description = models.CharField(max_length=300)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    expected_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING, db_index=True)
    reconciled_with = models.ForeignKey(
        Transaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="reconciled_pending"
    )
    batch = models.ForeignKey(
        ImportBatch, null=True, blank=True, on_delete=models.SET_NULL, related_name="pending_entries"
    )
    dedupe_key = models.CharField(max_length=64, null=True, blank=True, unique=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["expected_date", "id"]
        verbose_name_plural = "pending entries"

    def __str__(self):
        return f"{self.description} ({self.amount})"
