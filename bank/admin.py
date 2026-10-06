from decimal import Decimal

from django.contrib import admin, messages
from django.utils import timezone

from .models import BankAccount, BankReconciliation, BankStatementLine, BankTransaction
from .services import auto_match, calculate_book_balance, complete, post_bank_transaction


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class BankStatementLineInline(admin.TabularInline):
    model = BankStatementLine
    extra = 0
    fields = ['statement_date', 'value_date', 'description', 'reference', 'debit', 'credit',
              'balance', 'matched_transaction', 'match_type']
    autocomplete_fields = ['matched_transaction']


@admin.register(BankAccount)
class BankAccountAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['bank_name', 'account_number', 'account_name', 'company', 'branch',
                    'account_type', 'currency', 'gl_account', 'is_default', 'status']
    list_filter = ['company', 'account_type', 'status', 'currency']
    search_fields = ['bank_name', 'account_number', 'account_name']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(BankTransaction)
class BankTransactionAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['transaction_number', 'transaction_date', 'company', 'bank_account',
                    'tx_type', 'description', 'amount', 'status', 'reconciliation']
    list_filter = ['company', 'status', 'tx_type', 'transaction_date']
    search_fields = ['transaction_number', 'description']
    date_hierarchy = 'transaction_date'
    actions = ['post_to_gl']
    mutating_actions = ['post_to_gl']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs

    @admin.action(description='Post ke GL (cash/bank)')
    def post_to_gl(self, request, queryset):
        posted, errors = 0, []
        for tx in queryset.filter(status='DRAFT', journal__isnull=True):
            try:
                post_bank_transaction(tx, user=request.user)
                posted += 1
            except Exception as exc:
                errors.append(f'{tx.transaction_number}: {exc}')
        if posted:
            self.message_user(request, f'{posted} transaksi diposting ke GL.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)


@admin.register(BankReconciliation)
class BankReconciliationAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['reconciliation_name', 'bank_account', 'statement_date', 'status',
                    'statement_balance', 'book_balance', 'difference', 'unmatched_count']
    list_filter = ['status', 'bank_account__company']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))
    inlines = [BankStatementLineInline]
    actions = ['run_auto_match', 'refresh_book_balance', 'complete_reconciliation']
    mutating_actions = ['run_auto_match', 'refresh_book_balance', 'complete_reconciliation']
    readonly_fields = ['status', 'statement_balance', 'book_balance', 'completed_by', 'completed_at']
    change_form_template = 'admin/bank/bankreconciliation/change_form.html'

    def save_model(self, request, obj, form, change):
        from .services import import_statement_from_csv

        super().save_model(request, obj, form, change)
        uploaded = request.FILES.get('statement_csv')
        if not uploaded:
            return
        if hasattr(uploaded, 'seek'):
            uploaded.seek(0)
        created, errors = import_statement_from_csv(obj.bank_account, obj, uploaded)
        msg = f'{obj.reconciliation_name}: {len(created)} baris statement diimport.'
        if errors:
            msg += f' {len(errors)} baris dilewati.'
        self.message_user(request, msg)
        for err in errors[:10]:
            self.message_user(
                request, f'Baris {err["line"]}: {err["reason"]}', level=messages.WARNING
            )

    @admin.display(description='Selisih')
    def difference(self, obj):
        return obj.difference

    @admin.display(description='Unmatched')
    def unmatched_count(self, obj):
        return obj.unmatched_count

    @admin.action(description='Auto match statement vs transaksi')
    def run_auto_match(self, request, queryset):
        for rec in queryset:
            matched, unmatched = auto_match(rec)
            self.message_user(
                request, f'{rec.reconciliation_name}: {matched} matched, {unmatched} unmatched.'
            )

    @admin.action(description='Hitung ulang book balance')
    def refresh_book_balance(self, request, queryset):
        for rec in queryset:
            rec.book_balance = calculate_book_balance(rec.bank_account, rec.statement_date)
            rec.save(update_fields=['book_balance'])
        self.message_user(request, 'Book balance dihitung ulang.')

    @admin.action(description='Selesaikan rekonsiliasi')
    def complete_reconciliation(self, request, queryset):
        for rec in queryset:
            try:
                complete(rec, user=request.user)
                self.message_user(request, f'{rec.reconciliation_name} selesai.')
            except ValueError as exc:
                self.message_user(request, f'{rec.reconciliation_name}: {exc}', level=messages.ERROR)