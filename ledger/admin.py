from django.contrib import admin

from .models import (
    Appointment,
    BudgetLine,
    Category,
    CategoryRule,
    FiscalYearRate,
    ImportBatch,
    ImportProfile,
    PendingEntry,
    Person,
    PersonKey,
    PersonType,
    Transaction,
    Worktag,
)


class BudgetLineInline(admin.TabularInline):
    model = BudgetLine
    extra = 0


@admin.register(Worktag)
class WorktagAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "type", "start_date", "end_date", "fa_rate", "is_active"]
    list_filter = ["type", "is_active"]
    search_fields = ["code", "name", "match_codes"]
    inlines = [BudgetLineInline]


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ["date", "amount", "worktag", "category", "person", "source", "description"]
    list_filter = ["source", "category", "worktag"]
    search_fields = ["description", "reference", "raw_category", "raw_worktags"]
    date_hierarchy = "date"
    raw_id_fields = ["batch"]


@admin.register(PendingEntry)
class PendingEntryAdmin(admin.ModelAdmin):
    list_display = ["description", "amount", "worktag", "category", "expected_date", "status"]
    list_filter = ["status", "worktag"]
    raw_id_fields = ["reconciled_with", "batch"]


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ["alias", "person_type", "is_active"]


@admin.register(PersonKey)
class PersonKeyAdmin(admin.ModelAdmin):
    list_display = ["person", "kind", "created_at"]
    readonly_fields = ["key_hash"]


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ["filename", "source", "status", "created_at", "committed_at"]
    list_filter = ["status", "source"]
    exclude = ["sheets", "person_tokens"]


class FiscalYearRateInline(admin.TabularInline):
    model = FiscalYearRate
    extra = 0


@admin.register(PersonType)
class PersonTypeAdmin(admin.ModelAdmin):
    list_display = ["name", "sort_order"]
    inlines = [FiscalYearRateInline]


admin.site.register([Category, CategoryRule, Appointment, ImportProfile])
