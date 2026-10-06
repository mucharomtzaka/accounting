from django.contrib import admin, messages
from django.utils import timezone

from .models import AssetCategory, AssetDepreciation, AssetTransaction, FixedAsset
from .services import acquire_asset, depreciate_asset, dispose_asset, next_asset_number


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class AssetDepreciationInline(admin.TabularInline):
    model = AssetDepreciation
    extra = 0
    readonly_fields = ['period', 'depreciation_date', 'depreciation_amount', 'accumulated_before',
                       'accumulated_after', 'net_book_value_before', 'net_book_value_after', 'journal']
    can_delete = False


class AssetTransactionInline(admin.TabularInline):
    model = AssetTransaction
    extra = 0
    can_delete = False


@admin.register(AssetCategory)
class AssetCategoryAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['category_code', 'category_name', 'company', 'useful_life_years', 'is_active']
    list_filter = ['company', 'is_active']
    search_fields = ['category_code', 'category_name']
    autocomplete_fields = ['asset_account', 'accumulated_account', 'depreciation_account',
                           'disposal_gain_account', 'disposal_loss_account']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(FixedAsset)
class FixedAssetAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['asset_code', 'asset_name', 'company', 'branch', 'asset_category',
                    'acquisition_date', 'acquisition_cost', 'accumulated_depreciation',
                    'net_book_value', 'status']
    list_filter = ['company', 'status', 'asset_category']
    search_fields = ['asset_code', 'asset_name', 'serial_number']
    inlines = [AssetDepreciationInline, AssetTransactionInline]
    actions = ['acquire', 'depreciate', 'dispose', 'full_depreciate']
    mutating_actions = ['acquire', 'depreciate', 'dispose', 'full_depreciate']
    readonly_fields = ['status', 'accumulated_depreciation', 'net_book_value',
                       'last_depreciation_date', 'acquisition_journal', 'disposal_journal']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        if not change:
            obj.created_by = request.user
            if not obj.asset_code:
                obj.asset_code = next_asset_number(obj.company)
        super().save_model(request, obj, form, change)

    def _run_action(self, request, queryset, fn, ok_label):
        ok, errors = 0, []
        for asset in queryset:
            try:
                fn(asset, user=request.user)
                ok += 1
            except Exception as exc:
                errors.append(f'{asset.asset_code}: {exc}')
        if ok:
            self.message_user(request, f'{ok} asset {ok_label}.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)

    @admin.action(description='Acquire (DRAFT -> ACTIVE)')
    def acquire(self, request, queryset):
        self._run_action(request, queryset, acquire_asset, 'di-acquire')

    @admin.action(description='Depreciate bulan ini')
    def depreciate(self, request, queryset):
        self._run_action(request, queryset, depreciate_asset, 'didepresiasi')

    @admin.action(description='Dispose (nilai 0, catat di history)')
    def dispose(self, request, queryset):
        self._run_action(request, queryset, dispose_asset, 'di-dispose')

    @admin.action(description='Force FULLY_DEPRECIATED (tanpa jurnal)')
    def full_depreciate(self, request, queryset):
        # Escape hatch untuk asset yang diwariskan / data lama, journal tidak digenerate.
        count = queryset.filter(status='ACTIVE').update(status='FULLY_DEPRECIATED')
        queryset.filter(status='FULLY_DEPRECIATED').update(net_book_value=0)
        self.message_user(request, f'{count} asset ditandai fully depreciated.')


@admin.register(AssetDepreciation)
class AssetDepreciationAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['asset', 'period', 'depreciation_date', 'depreciation_amount',
                    'accumulated_after', 'net_book_value_after']
    list_filter = ['asset__company', 'period__status']
    date_hierarchy = 'depreciation_date'

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('asset', 'period', 'journal')
        return qs.filter(asset__company__in=request.user.allowed_companies(request))

    def has_add_permission(self, request):
        return False


@admin.register(AssetTransaction)
class AssetTransactionAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['asset', 'tx_type', 'transaction_date', 'amount', 'description']
    list_filter = ['asset__company', 'tx_type']

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('asset')
        return qs.filter(asset__company__in=request.user.allowed_companies(request))

    def has_add_permission(self, request):
        return False