from decimal import Decimal
from pathlib import PurePath

from django import forms
from django.conf import settings
from django.utils import timezone

from .models import (
    IMPORT_SOURCE_CHOICES,
    Appointment,
    BudgetLine,
    Category,
    CategoryRule,
    FiscalYearRate,
    Funding,
    PendingEntry,
    Person,
    PersonType,
    Transaction,
    Worktag,
)
from .services.importer import DEFAULT_SKIP_STATUSES
from .services.rates import RateTable
from .services.mapping import FIELDS
from .services.values import parse_month_header

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
MONEY_WIDGET = forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"})


class WorktagForm(forms.ModelForm):
    """Add or edit a fund. Fields that only matter for one kind of fund are shown by data-when (see app.js)."""

    amount = forms.DecimalField(
        label="Amount",
        required=False,
        min_value=0,
        max_digits=14,
        decimal_places=2,
        widget=MONEY_WIDGET,
        help_text="The award, the gift, or this fiscal year's state allocation. More can be added later from the fund page.",
    )
    funding_date = forms.DateField(
        label="Money available as of",
        required=False,
        widget=DATE_WIDGET,
        help_text="State funds: use a date inside the fiscal year (July to June) the money is for.",
    )

    class Meta:
        model = Worktag
        fields = [
            "type", "code", "name", "sponsor", "start_date", "end_date", "fa_rate", "fa_actuals",
            "match_codes", "notes", "is_active",
        ]
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "match_codes": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {"end_date": "When the award ends. Runway and warnings count down to this date."}

    GRANT_ONLY = ("sponsor", "start_date", "end_date", "fa_rate", "fa_actuals")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.creating = self.instance.pk is None
        for name in self.GRANT_ONLY:
            self.fields[name].widget.attrs["data-when"] = "type=grant"
        if self.creating:
            self.fields["funding_date"].initial = timezone.localdate
        else:
            # Money is managed on the fund page once the fund exists.
            del self.fields["amount"], self.fields["funding_date"]

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date must be on or after the start date.")
        # Every ID must point at exactly one fund, or imported rows become ambiguous.
        mine = {line.strip().upper() for line in [data.get("code") or "", *(data.get("match_codes") or "").splitlines()]}
        mine.discard("")
        others = Worktag.objects.exclude(pk=self.instance.pk) if self.instance.pk else Worktag.objects.all()
        for other in others:
            clash = mine & {code.upper() for code in other.all_codes}
            if clash:
                field = "code" if (data.get("code") or "").upper() in clash else "match_codes"
                self.add_error(field, f"{', '.join(sorted(clash))} already belongs to {other.code} ({other.name}).")
                break
        return data

    def save(self, commit=True):
        worktag = super().save(commit=commit)
        amount = self.cleaned_data.get("amount")
        if commit and self.creating and amount:
            Funding.objects.create(
                worktag=worktag,
                amount=amount,
                date=self.cleaned_data.get("funding_date") or timezone.localdate(),
                description="Initial funding",
            )
        return worktag


class FundingForm(forms.ModelForm):
    class Meta:
        model = Funding
        fields = ["date", "amount", "description"]
        widgets = {"date": DATE_WIDGET, "amount": MONEY_WIDGET}
        help_texts = {
            "amount": "Use a negative amount for money taken back.",
            "description": "For example: award, top-up, FY2027 allocation.",
        }


class BudgetForm(forms.Form):
    """One amount per category; leave blank for no budget line."""

    def __init__(self, *args, worktag=None, **kwargs):
        super().__init__(*args, **kwargs)
        existing = {line.category_id: line.amount for line in worktag.budget_lines.all()} if worktag and worktag.pk else {}
        self.categories = list(Category.objects.all())
        for category in self.categories:
            self.fields[f"cat_{category.pk}"] = forms.DecimalField(
                label=category.name, required=False, max_digits=14, decimal_places=2, min_value=0,
                initial=existing.get(category.pk), widget=MONEY_WIDGET,
            )

    def save(self, worktag):
        for category in self.categories:
            amount = self.cleaned_data.get(f"cat_{category.pk}")
            if amount is None:
                BudgetLine.objects.filter(worktag=worktag, category=category).delete()
            else:
                BudgetLine.objects.update_or_create(worktag=worktag, category=category, defaults={"amount": amount})


class TransactionForm(forms.ModelForm):
    class Meta:
        model = Transaction
        fields = ["worktag", "category", "person", "date", "amount", "description", "reference", "notes"]
        widgets = {"date": DATE_WIDGET, "amount": MONEY_WIDGET, "notes": forms.Textarea(attrs={"rows": 2})}
        help_texts = {"amount": "Positive for spending, negative for refunds or credits."}


class BulkAssignForm(forms.Form):
    worktag = forms.ModelChoiceField(Worktag.objects.all(), required=False, label="Set fund")
    category = forms.ModelChoiceField(Category.objects.all(), required=False, label="Set category")


class PendingEntryForm(forms.ModelForm):
    class Meta:
        model = PendingEntry
        fields = ["worktag", "category", "person", "description", "amount", "expected_date", "status", "notes"]
        widgets = {"expected_date": DATE_WIDGET, "amount": MONEY_WIDGET, "notes": forms.Textarea(attrs={"rows": 2})}


class PersonForm(forms.ModelForm):
    class Meta:
        model = Person
        fields = ["alias", "person_type", "notes", "is_active"]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class MergePersonForm(forms.Form):
    target = forms.ModelChoiceField(Person.objects.none(), label="Merge into")

    def __init__(self, *args, person, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["target"].queryset = Person.objects.exclude(pk=person.pk)


class AppointmentForm(forms.ModelForm):
    class Meta:
        model = Appointment
        fields = [
            "person", "worktag", "kind", "start_date", "end_date", "monthly_salary", "effort_pct",
            "hourly_rate", "hours_per_week", "fringe_rate_override", "tuition_monthly_override", "notes",
        ]
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "monthly_salary": MONEY_WIDGET,
            "hourly_rate": MONEY_WIDGET,
            "tuition_monthly_override": MONEY_WIDGET,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("monthly_salary", "effort_pct"):
            self.fields[name].widget.attrs["data-when"] = "kind=regular,summer"
        for name in ("hourly_rate", "hours_per_week"):
            self.fields[name].widget.attrs["data-when"] = "kind=hourly"

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        if kind == Appointment.Kind.HOURLY:
            data["effort_pct"] = Decimal("100")
            data["monthly_salary"] = Decimal("0")
        elif kind and data.get("monthly_salary") is None:
            self.add_error("monthly_salary", "Enter the monthly salary.")
        return data


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "code", "fa_eligible", "sort_order"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.is_system:
            self.fields["code"].disabled = True
            self.fields["code"].help_text = "Built-in"


CategoryFormSet = forms.modelformset_factory(Category, form=CategoryForm, extra=1, can_delete=True)
PersonTypeFormSet = forms.modelformset_factory(PersonType, fields=["name", "sort_order"], extra=1, can_delete=True)
CategoryRuleFormSet = forms.modelformset_factory(
    CategoryRule, fields=["priority", "keywords", "category"], extra=1, can_delete=True,
    widgets={"keywords": forms.TextInput(attrs={"size": 60})},
)

class FiscalYearRatesForm(forms.Form):
    """Grid of fringe rates and monthly tuition: person types as rows, fiscal years as columns.

    Blank cells mean "reuse the closest earlier year"; their placeholder shows the value that applies.
    """

    METRICS = {
        "fringe": ("fringe_rate", {"max_digits": 6, "decimal_places": 3, "max_value": 100}, "0.001"),
        "tuition": ("tuition_monthly", {"max_digits": 10, "decimal_places": 2}, "0.01"),
    }

    def __init__(self, *args, fiscal_years, person_types, **kwargs):
        super().__init__(*args, **kwargs)
        self.fiscal_years = list(fiscal_years)
        self.person_types = list(person_types)
        rows = list(FiscalYearRate.objects.filter(person_type__in=self.person_types))
        existing = {(row.person_type_id, row.fiscal_year): row for row in rows}
        table = RateTable(rows)
        for person_type in self.person_types:
            for year in self.fiscal_years:
                row = existing.get((person_type.pk, year))
                for prefix, (metric, limits, step) in self.METRICS.items():
                    value = getattr(row, metric) if row else None
                    attrs = {"step": step, "inputmode": "decimal", "aria-label": f"{person_type.name}, FY{year}"}
                    if value is None:
                        carried, source = table.lookup(person_type.pk, metric, year)
                        if source is not None:
                            attrs["placeholder"] = f"{format(carried.normalize(), 'f')} (FY{source})"
                    self.fields[f"{prefix}_{person_type.pk}_{year}"] = forms.DecimalField(
                        required=False, min_value=0, initial=value, widget=forms.NumberInput(attrs=attrs), **limits
                    )

    def _rows(self, prefix):
        return [
            (person_type, [self[f"{prefix}_{person_type.pk}_{year}"] for year in self.fiscal_years])
            for person_type in self.person_types
        ]

    @property
    def fringe_rows(self):
        return self._rows("fringe")

    @property
    def tuition_rows(self):
        return self._rows("tuition")

    def save(self):
        for person_type in self.person_types:
            for year in self.fiscal_years:
                values = {
                    metric: self.cleaned_data.get(f"{prefix}_{person_type.pk}_{year}")
                    for prefix, (metric, _, _) in self.METRICS.items()
                }
                if all(value is None for value in values.values()):
                    FiscalYearRate.objects.filter(person_type=person_type, fiscal_year=year).delete()
                else:
                    FiscalYearRate.objects.update_or_create(
                        person_type=person_type, fiscal_year=year, defaults=values
                    )


ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".xls", ".csv", ".tsv", ".txt", ".htm", ".html", ".mht", ".mhtml"}


class UploadForm(forms.Form):
    source = forms.ChoiceField(choices=IMPORT_SOURCE_CHOICES, label="What is this file?")
    file = forms.FileField(help_text="Excel (.xlsx, .xls), CSV, or PeopleSoft download. Max 10 MB.")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if PurePath(upload.name).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise forms.ValidationError("Upload an Excel, CSV, or HTML export.")
        if upload.size > settings.IMPORT_MAX_BYTES:
            raise forms.ValidationError("The file is larger than 10 MB.")
        return upload


class MappingForm(forms.Form):
    sheet_name = forms.ChoiceField(label="Sheet / table")
    header_row = forms.IntegerField(label="Header row", min_value=1, help_text="Row number that holds the column names.")
    import_as = forms.ChoiceField(
        label="Import rows as",
        choices=[("actual", "Actual spending"), ("pending", "Pending / planned entries")],
        widget=forms.RadioSelect,
    )
    default_worktag = forms.TypedChoiceField(
        label="Fund when no worktag matches", required=False, coerce=int, empty_value=None,
        help_text="Use when the whole file belongs to one fund.",
    )
    default_category = forms.ChoiceField(label="Category when no rule matches", required=False)
    skip_statuses = forms.CharField(
        label="Skip rows whose status contains", required=False, help_text="Comma-separated words."
    )
    negate = forms.BooleanField(label="Flip signs (file shows spending as negative)", required=False)
    skip_zero = forms.BooleanField(label="Skip zero amounts", required=False)
    day_first = forms.BooleanField(label="Dates are day/month/year", required=False)

    OPTION_FIELDS = ("import_as", "default_worktag", "default_category", "skip_statuses", "negate", "skip_zero", "day_first")

    def __init__(self, *args, batch, headers, **kwargs):
        super().__init__(*args, **kwargs)
        self.specs = [spec for spec in FIELDS if not (spec.payroll_only and batch.source == "workday_expense")]
        header_choices = [(header, header) for header in headers]
        self.fields["sheet_name"].choices = [(name, name) for name in batch.sheet_names]
        self.fields["default_worktag"].choices = [("", "— leave unassigned —")] + [
            (w.pk, str(w)) for w in Worktag.objects.all()
        ]
        self.fields["default_category"].choices = [("", "— leave uncategorized —")] + [
            (c.code, c.name) for c in Category.objects.all()
        ]
        for spec in self.specs:
            if spec.multi:
                field = forms.MultipleChoiceField(
                    choices=header_choices, required=False, widget=forms.CheckboxSelectMultiple
                )
            else:
                field = forms.ChoiceField(choices=[("", "— not in file —")] + header_choices, required=False)
            field.label, field.help_text = spec.label, spec.help
            self.fields[f"map_{spec.key}"] = field
        if not self.is_bound:
            self.initial.setdefault("skip_statuses", DEFAULT_SKIP_STATUSES)

    @property
    def mapping_fields(self):
        return [self[f"map_{spec.key}"] for spec in self.specs]

    @property
    def option_fields(self):
        return [self[name] for name in self.OPTION_FIELDS]

    def clean(self):
        data = super().clean()
        amounts = data.get("map_amount") or []
        if not amounts:
            self.add_error("map_amount", "Choose the amount column.")
        if not data.get("map_date"):
            unnamed = [header for header in amounts if parse_month_header(header) is None]
            if len(amounts) <= 1 or unnamed:
                self.add_error("map_date", "Choose the date column (or amount columns named like months, e.g. 'Jul 2026').")
        return data

    def column_map(self) -> dict:
        return {spec.key: self.cleaned_data.get(f"map_{spec.key}") or ([] if spec.multi else "") for spec in self.specs}

    def options(self) -> dict:
        return {name: self.cleaned_data.get(name) for name in self.OPTION_FIELDS}
