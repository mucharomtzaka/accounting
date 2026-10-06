from decimal import Decimal

from django.contrib import admin, messages
from django.utils import timezone

from .models import (
    Customer, CustomerInvoice, CustomerInvoiceLine, CustomerPayment,
    InvoiceStatus, PaymentAllocation,
)
from .services import (
    allocate_payment, next_invoice_number, post_invoice, post_payment, recalc_invoice_totals,
)


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class CustomerInvoiceLineInline(admin.TabularInline):
    model = CustomerInvoiceLine
    extra = 1
    fields = ['line_no', 'product', 'account', 'description', 'quantity', 'unit_price',
              'discount', 'tax_code', 'tax_rate', 'total', 'project']
    autocomplete_fields = ['product', 'account', 'tax_code', 'project']


class PaymentAllocationInline(admin.TabularInline):
    model = PaymentAllocation
    extra = 0
    fields = ['invoice', 'amount']
    autocomplete_fields = ['invoice']


class ARInvoiceAdminMixin(CompanyScopedFiltersMixin):
    """Filter list per company milik user (Rule 1 - company isolation)."""

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        if not change:
            obj.created_by = request.user
            if not obj.invoice_number:
                obj.invoice_number = next_invoice_number(obj.company, obj.invoice_type)
        super().save_model(request, obj, form, change)


@admin.register(Customer)
class CustomerAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['customer_code', 'customer_name', 'company', 'currency', 'payment_term_days',
                    'credit_limit', 'status']
    list_filter = ['company', 'status', 'currency']
    search_fields = ['customer_code', 'customer_name', 'npwp']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(CustomerInvoice)
class CustomerInvoiceAdmin(ARInvoiceAdminMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['invoice_number', 'invoice_date', 'due_date', 'company', 'customer',
                    'currency', 'total', 'amount_paid', 'outstanding', 'status']
    list_filter = ['company', 'status', 'invoice_type', 'invoice_date']
    search_fields = ['invoice_number', 'customer__customer_name']
    date_hierarchy = 'invoice_date'
    inlines = [CustomerInvoiceLineInline]
    actions = ['recalculate', 'post_invoice']
    mutating_actions = ['recalculate', 'post_invoice']
    readonly_fields = ['status', 'subtotal', 'tax', 'total', 'amount_paid', 'outstanding', 'journal']

    @admin.display(description='Outstanding')
    def outstanding(self, obj):
        return obj.outstanding

    @admin.action(description='Hitung ulang total dari line')
    def recalculate(self, request, queryset):
        for invoice in queryset:
            recalc_invoice_totals(invoice)
        self.message_user(request, f'{queryset.count()} invoice dihitung ulang.')

    @admin.action(description='Post ke GL + buat journal otomatis')
    def post_invoice(self, request, queryset):
        posted, errors = 0, []
        for invoice in queryset:
            try:
                post_invoice(invoice, user=request.user)
                posted += 1
            except Exception as exc:
                errors.append(f'{invoice.invoice_number}: {exc}')
        if posted:
            self.message_user(request, f'{posted} invoice diposting ke GL.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)


@admin.register(CustomerInvoiceLine)
class CustomerInvoiceLineAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['invoice', 'line_no', 'description', 'quantity', 'unit_price', 'tax_rate', 'total']
    list_filter = ['invoice__company']

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('invoice', 'product', 'account', 'tax_code', 'project')
        return qs.filter(invoice__company__in=request.user.allowed_companies(request))

    def has_add_permission(self, request):
        return False


@admin.register(CustomerPayment)
class CustomerPaymentAdmin(ARInvoiceAdminMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['payment_number', 'payment_date', 'company', 'customer', 'bank_account',
                    'amount', 'amount_allocated', 'bank_fee', 'unallocated', 'status']
    list_filter = ['company', 'status', 'payment_date']
    search_fields = ['payment_number', 'customer__customer_name']
    inlines = [PaymentAllocationInline]
    actions = ['post_payment']
    mutating_actions = ['post_payment']
    readonly_fields = ['amount_allocated', 'status', 'journal', 'payment_number']

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