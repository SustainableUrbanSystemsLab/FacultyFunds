from pathlib import PurePath

from django import forms
from django.conf import settings

from .models import (
    IMPORT_SOURCE_CHOICES,
    Appointment,
    BudgetLine,
    Category,
    CategoryRule,
    PendingEntry,
    Person,
    PersonType,
    Transaction,
    Worktag,
)
from .services.importer import DEFAULT_SKIP_STATUSES
from .services.mapping import FIELDS
from .services.values import parse_month_header

DATE_WIDGET = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
MONEY_WIDGET = forms.NumberInput(attrs={"step": "0.01", "inputmode": "decimal"})


class WorktagForm(forms.ModelForm):
    class Meta:
        model = Worktag
        fields = [
            "type", "code", "name", "sponsor", "start_date", "end_date", "fa_rate", "fa_actuals",
            "match_codes", "tracking_start", "notes", "is_active",
        ]
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "tracking_start": DATE_WIDGET,
            "match_codes": forms.Textarea(attrs={"rows": 3}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def clean_code(self):
        return self.cleaned_data["code"].strip()

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "End date must be on or after the start date.")
        return data


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
            "fringe_rate_override", "tuition_monthly_override", "notes",
        ]
        widgets = {
            "start_date": DATE_WIDGET,
            "end_date": DATE_WIDGET,
            "monthly_salary": MONEY_WIDGET,
            "tuition_monthly_override": MONEY_WIDGET,
        }


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
PersonTypeFormSet = forms.modelformset_factory(
    PersonType, fields=["name", "fringe_rate", "tuition_monthly", "sort_order"], extra=1, can_delete=True
)
CategoryRuleFormSet = forms.modelformset_factory(
    CategoryRule, fields=["priority", "keywords", "category"], extra=1, can_delete=True,
    widgets={"keywords": forms.TextInput(attrs={"size": 60})},
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
