from django.contrib import admin, messages

from .models import (
    PaymentAllocation, Supplier, SupplierBill, SupplierBillLine, SupplierPayment, BillStatus,
)
from .services import next_bill_number, post_bill, post_payment, recalc_bill_totals, three_way_match


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class SupplierBillLineInline(admin.TabularInline):
    model = SupplierBillLine
    extra = 1
    fields = ['line_no', 'product', 'account', 'description', 'quantity', 'unit_price',
              'discount', 'tax_code', 'tax_rate', 'total', 'po_number', 'receipt_number', 'project']
    autocomplete_fields = ['product', 'account', 'tax_code', 'project']


class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    fields = ['bill', 'amount']
    autocomplete_fields = ['bill']


class APBaseAdmin(CompanyScopedFiltersMixin):
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Supplier)
class SupplierAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['supplier_code', 'supplier_name', 'company', 'currency',
                    'is_intercompany', 'intercompany_company', 'status']
    list_filter = ['company', 'status', 'is_intercompany']
    search_fields = ['supplier_code', 'supplier_name', 'npwp']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(SupplierBill)
class SupplierBillAdmin(APBaseAdmin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['bill_number', 'bill_date', 'due_date', 'company', 'supplier',
                    'total', 'amount_paid', 'outstanding', 'status']
    list_filter = ['company', 'status', 'bill_type', 'bill_date']
    search_fields = ['bill_number', 'supplier_reference', 'supplier__supplier_name']
    date_hierarchy = 'bill_date'
    inlines = [SupplierBillLineInline]
    actions = ['recalculate', 'post_bill', 'three_way_match']
    mutating_actions = ['recalculate', 'post_bill', 'three_way_match']
    readonly_fields = ['status', 'subtotal', 'tax', 'total', 'amount_paid', 'outstanding', 'journal',
                       'withholding_tax']

    def save_model(self, request, obj, form, change):
        if not change and not obj.bill_number:
            obj.bill_number = next_bill_number(obj.company, obj.bill_type)
        super().save_model(request, obj, form, change)

    @admin.display(description='Outstanding')
    def outstanding(self, obj):
        return obj.outstanding

    @admin.action(description='Hitung ulang total dari line')
    def recalculate(self, request, queryset):
        for bill in queryset:
            recalc_bill_totals(bill)
        self.message_user(request, f'{queryset.count()} bill dihitung ulang.')

    @admin.action(description='Post ke GL + buat journal otomatis')
    def post_bill(self, request, queryset):
        posted, errors = 0, []
        for bill in queryset:
            try:
                post_bill(bill, user=request.user)
                posted += 1
            except Exception as exc:
                errors.append(f'{bill.bill_number}: {exc}')
        if posted:
            self.message_user(request, f'{posted} bill diposting ke GL.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)

    @admin.action(description='Cek three-way match (PO + Receipt + Invoice)')
    def three_way_match(self, request, queryset):
        for bill in queryset:
            exceptions = three_way_match(bill)
            if exceptions:
                for exc in exceptions:
                    self.message_user(request, f'{bill.bill_number}: {exc["message"]}',
                                      level=messages.WARNING)
            else:
                self.message_user(request, f'{bill.bill_number}: match OK.')


@admin.register(SupplierPayment)
class SupplierPaymentAdmin(APBaseAdmin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['payment_number', 'payment_date', 'company', 'supplier', 'bank_account',
                    'amount', 'amount_allocated', 'bank_fee', 'unallocated', 'status']
    list_filter = ['company', 'status', 'payment_date']
    search_fields = ['payment_number', 'supplier__supplier_name']
    inlines = [PaymentAllocationInline]
    actions = ['post_payment']
    mutating_actions = ['post_payment']
    readonly_fields = ['amount_allocated', 'status', 'journal']

    @admin.display(description='Unallocated')
    def unallocated(self, obj):
        return obj.unallocated

    @admin.action(description='Post ke GL')
    def post_payment(self, request, queryset):
        posted, errors = 0, []
        for payment in queryset:
            try:
                post_payment(payment, user=request.user)
                posted += 1
            except Exception as exc:
                errors.append(f'{payment.payment_number}: {exc}')
        if posted:
            self.message_user(request, f'{posted} payment diposting ke GL.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)


@admin.register(SupplierBillLine)
class SupplierBillLineAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['bill', 'line_no', 'description', 'quantity', 'unit_price', 'total',
                    'po_number', 'receipt_number']
    list_filter = ['bill__company']

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('bill', 'product', 'account', 'tax_code', 'project')
        return qs.filter(bill__company__in=request.user.allowed_companies(request))

    def has_add_permission(self, request):
        return False