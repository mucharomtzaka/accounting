from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (
    Inventory, InventoryCost, InventoryTransaction, StockOpname, StockOpnameLine, Warehouse,
)
from .services import post_stock_opname, refresh_system_quantity


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class StockOpnameLineInline(admin.TabularInline):
    model = StockOpnameLine
    extra = 0
    fields = ['inventory', 'system_quantity', 'counted_quantity', 'unit_cost', 'variance_display', 'notes']
    readonly_fields = ['variance_display']
    autocomplete_fields = ['inventory']

    @admin.display(description='Variance')
    def variance_display(self, obj):
        return obj.variance if obj and obj.pk else '-'


@admin.register(StockOpname)
class StockOpnameAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['opname_number', 'opname_date', 'company', 'warehouse', 'status',
                    'lines_count', 'variance_total']
    list_filter = ['company', 'warehouse', 'status', 'opname_date']
    search_fields = ['opname_number', 'notes']
    date_hierarchy = 'opname_date'
    inlines = [StockOpnameLineInline]
    readonly_fields = ['counted_by', 'counted_at', 'posted_by', 'posted_at']
    actions = ['snapshot_system_quantity', 'mark_counted', 'post_opname']
    mutating_actions = ['snapshot_system_quantity', 'mark_counted', 'post_opname']
    list_select_related = ['company', 'warehouse']
    fieldsets = (
        (None, {'fields': ('company', 'warehouse', 'opname_number', 'opname_date', 'status')}),
        ('Notes', {'fields': ('notes',)}),
        ('Audit', {'fields': ('counted_by', 'counted_at', 'posted_by', 'posted_at')}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request).prefetch_related('lines')
        return qs.filter(company__in=request.user.allowed_companies(request))

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.status in (StockOpname.Status.POSTED, StockOpname.Status.CANCELLED):
            return base + ['company', 'warehouse', 'opname_number', 'opname_date', 'status']
        return base

    def has_delete_permission(self, request, obj=None):
        # Dokumen yang sudah posted jadi jejak audit; batalkan (CANCELLED) saja.
        if obj and obj.status == StockOpname.Status.POSTED:
            return False
        return super().has_delete_permission(request, obj)

    @admin.display(description='Lines')
    def lines_count(self, obj):
        return obj.lines.count()

    @admin.display(description='Variance')
    def variance_total(self, obj):
        return obj.variance_total

    @admin.action(description='Ambil qty sistem ke semua baris (bekukan snapshot)')
    def snapshot_system_quantity(self, request, queryset):
        done = 0
        for opname in queryset:
            if not opname.is_editable:
                self.message_user(request, f'{opname.opname_number}: status {opname.status} tidak bisa diubah.',
                                  level=messages.WARNING)
                continue
            refresh_system_quantity(opname)
            done += 1
        if done:
            self.message_user(request, f'{done} stock opname: qty sistem di-snapshot.')

    @admin.action(description='Tandai selesai dihitung (COUNTED)')
    def mark_counted(self, request, queryset):
        updated = queryset.filter(status=StockOpname.Status.DRAFT).update(
            status=StockOpname.Status.COUNTED,
            counted_by=request.user, counted_at=timezone.now(),
        )
        if updated:
            self.message_user(request, f'{updated} stock opname ditandai COUNTED.')

    @admin.action(description='Posting variance ke Inventory + GL')
    def post_opname(self, request, queryset):
        done, errors = 0, []
        for opname in queryset:
            try:
                post_stock_opname(opname, user=request.user)
                done += 1
            except ValidationError as exc:
                errors.append(f'{opname.opname_number}: {"; ".join(exc.messages)}')
        if done:
            self.message_user(request, f'{done} stock opname diposting.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)


@admin.register(Warehouse)
class WarehouseAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['warehouse_code', 'warehouse_name', 'company', 'branch', 'status']
    list_filter = ['company', 'branch', 'status']
    search_fields = ['warehouse_code', 'warehouse_name']
    list_select_related = ['company', 'branch']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(Inventory)
class InventoryAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['product', 'company', 'branch', 'warehouse', 'quantity_on_hand',
                    'available', 'average_cost', 'valuation', 'costing_method', 'is_active']
    list_filter = ['company', 'warehouse', 'costing_method', 'is_active']
    search_fields = ['product__product_code', 'product__product_name',
                     'warehouse__warehouse_code', 'warehouse__warehouse_name']
    readonly_fields = ['quantity_on_hand', 'average_cost', 'last_cost', 'total_cost', 'valuation']
    list_select_related = ['product', 'company', 'branch', 'warehouse']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['product', 'tx_type', 'transaction_date', 'quantity', 'unit_cost',
                    'total_cost', 'reference', 'is_posted']
    list_filter = ['inventory__company', 'tx_type', 'is_posted', 'transaction_date']
    search_fields = ['product__product_code', 'reference']
    date_hierarchy = 'transaction_date'
    actions = ['post_to_gl']
    mutating_actions = ['post_to_gl']
    list_select_related = ['product', 'inventory', 'inventory__company']

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related(
            'product', 'inventory', 'inventory__company', 'journal')
        qs = qs.filter(inventory__company__in=request.user.allowed_companies(request))
        return qs

    @admin.action(description='Post ke GL (COGS / inventory)')
    def post_to_gl(self, request, queryset):
        from gl.engine import JournalBuilder, get_account
        from gl.models import JournalSource

        posted = 0
        for tx in queryset.filter(is_posted=True, journal__isnull=True):
            inv_account = get_account(tx.inventory.company, 'INVENTORY', 'INVENTORY_ASSET')
            cogs_account = get_account(tx.inventory.company, 'COGS', 'PRODUCT_COGS', required=False)
            if cogs_account is None:
                from gl.models import Account

                cogs_account = Account.objects.filter(
                    company=tx.inventory.company, account_category=5, is_postable=True
                ).first()

            if tx.is_in:
                journal = (
                    JournalBuilder(tx.inventory.company, tx.transaction_date,
                                   source=JournalSource.INVENTORY,
                                   description=f'{tx.tx_type} {tx.product.product_code}',
                                   branch=tx.inventory.branch,
                                   reference=tx.reference, user=request.user)
                    .debit(inv_account, tx.total_cost, 'Inventory')
                    .credit(get_account(tx.inventory.company, 'AP', 'AP_CONTROL', required=False) or
                            inv_account, tx.total_cost, 'Kredit inventory')
                    .save(auto_post=True)
                )
            else:
                if cogs_account is None:
                    self.message_user(
                        request, f'{tx.product.product_code}: akun COGS tidak ditemukan.',
                        level=messages.ERROR,
                    )
                    continue
                journal = (
                    JournalBuilder(tx.inventory.company, tx.transaction_date,
                                   source=JournalSource.INVENTORY,
                                   description=f'{tx.tx_type} {tx.product.product_code}',
                                   branch=tx.inventory.branch,
                                   reference=tx.reference, user=request.user)
                    .debit(cogs_account, tx.total_cost, 'COGS')
                    .credit(inv_account, tx.total_cost, 'Inventory')
                    .save(auto_post=True)
                )
            InventoryTransaction.objects.filter(pk=tx.pk).update(journal=journal)
            posted += 1
        self.message_user(request, f'{posted} transaksi diposting ke GL.')


@admin.register(InventoryCost)
class InventoryCostAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['inventory', 'transaction', 'quantity_in', 'quantity_out',
                    'quantity_balance', 'unit_cost', 'total_cost_balance', 'method']
    list_filter = ['inventory__company', 'method']

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('inventory', 'inventory__product', 'transaction')
        return qs.filter(inventory__company__in=request.user.allowed_companies(request))

    def has_add_permission(self, request):
        return False