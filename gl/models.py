from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from core.models import AuditMixin, TimeStampedModel, User
from .errors import AccountingError, PeriodLockedError, UnbalancedJournalError
from django.utils.translation import gettext_lazy as _


class AccountCategory(models.IntegerChoices):
    ASSET = 1, '1 - Asset'
    LIABILITY = 2, '2 - Liability'
    EQUITY = 3, '3 - Equity'
    REVENUE = 4, '4 - Revenue'
    COGS = 5, '5 - COGS'
    EXPENSE = 6, '6 - Expense'


class NormalBalance(models.TextChoices):
    DEBIT = 'D', 'Debit'
    CREDIT = 'C', 'Credit'


class AccountQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True)

    def posting(self):
        return self.filter(is_active=True, is_postable=True)


class Account(TimeStampedModel, AuditMixin):
    """PRD 9 - Chart of Accounts, company-specific COA with hierarchy."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='accounts')
    account_code = models.CharField(max_length=20)
    account_name = models.CharField(max_length=255)
    account_category = models.IntegerField(choices=AccountCategory.choices)
    account_type = models.CharField(max_length=50)
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children'
    )
    normal_balance = models.CharField(max_length=1, choices=NormalBalance.choices)
    currency = models.ForeignKey(
        'core.Currency', null=True, blank=True, on_delete=models.PROTECT, related_name='accounts'
    )
    is_control_account = models.BooleanField(
        default=False, help_text='Control account (AR/AP/Cash - kontrol dari modul sumber)'
    )
    is_reconciliation_account = models.BooleanField(
        default=False, help_text='Akun yang direkonsiliasi (bank, AR, AP)'
    )
    is_postable = models.BooleanField(default=True, help_text='False = akun header')
    is_system = models.BooleanField(default=False, help_text='Dipakai accounting engine, tidak boleh dihapus')
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    objects = AccountQuerySet.as_manager()

    class Meta:
        verbose_name = _('Account')
        verbose_name_plural = _('Accounts')
        ordering = ['account_code']
        unique_together = [('company', 'account_code')]

    def __str__(self):
        return f'{self.account_code} - {self.account_name}'

    @property
    def level(self):
        return len(self.account_code) // 2

    @property
    def normal_balance_sign(self):
        return Decimal('1') if self.normal_balance == NormalBalance.DEBIT else Decimal('-1')


class FiscalYear(TimeStampedModel):
    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='fiscal_years')
    name = models.CharField(max_length=20, help_text='Contoh: 2026')
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)
    closed_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Fiscal Year')
        verbose_name_plural = _('Fiscal Years')
        ordering = ['-start_date']
        unique_together = [('company', 'name')]

    def __str__(self):
        return f'{self.company.company_code} FY {self.name}'


class PeriodStatus(models.TextChoices):
    OPEN = 'OPEN', 'Open'
    CLOSING = 'CLOSING', 'Closing'
    CLOSED = 'CLOSED', 'Closed'


class AccountingPeriod(TimeStampedModel):
    """PRD 10 + Rule 3 - period locking."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='periods')
    fiscal_year = models.ForeignKey(FiscalYear, on_delete=models.CASCADE, related_name='periods')
    period_code = models.CharField(max_length=7, help_text='Contoh: 2026-01')
    period_name = models.CharField(max_length=50)
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=PeriodStatus.choices, default=PeriodStatus.OPEN)
    # OPEN -> CLOSING (review checklist) -> CLOSED (dikunci). closing_* = saat
    # masuk tahap review; closed_* = saat kunci final.
    closing_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    closing_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    closed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Accounting Period')
        verbose_name_plural = _('Accounting Periods')
        ordering = ['-start_date']
        unique_together = [('company', 'period_code')]

    def __str__(self):
        return f'{self.period_code} {self.period_name}'

    @property
    def is_locked(self):
        return self.status == PeriodStatus.CLOSED

    def contains(self, date):
        return self.start_date <= date <= self.end_date


class JournalSource(models.TextChoices):
    MANUAL = 'MANUAL', 'Manual Journal'
    SALES_INVOICE = 'SALES_INVOICE', 'Sales Invoice'
    PURCHASE_INVOICE = 'PURCHASE_INVOICE', 'Purchase Invoice'
    CUSTOMER_PAYMENT = 'CUSTOMER_PAYMENT', 'Customer Payment'
    SUPPLIER_PAYMENT = 'SUPPLIER_PAYMENT', 'Supplier Payment'
    BANK = 'BANK', 'Bank Transaction'
    INVENTORY = 'INVENTORY', 'Inventory'
    ASSET = 'ASSET', 'Fixed Asset'
    TAX = 'TAX', 'Tax'
    INTERCOMPANY = 'INTERCOMPANY', 'Intercompany'
    ACCRUAL = 'ACCRUAL', 'Accrual'
    CLOSING = 'CLOSING', 'Closing'
    OPENING_BALANCE = 'OPENING_BALANCE', 'Opening Balance'


class JournalStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    SUBMITTED = 'SUBMITTED', 'Submitted'
    APPROVED = 'APPROVED', 'Approved'
    POSTED = 'POSTED', 'Posted'
    RECONCILED = 'RECONCILED', 'Reconciled'
    VOID = 'VOID', 'Void'


class JournalEntry(TimeStampedModel, AuditMixin):
    """PRD 11 - Rule 2: POSTED immutable, Rule 4: balanced."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='journal_entries')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_entries'
    )
    department = models.ForeignKey(
        'core.Department', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_entries'
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_entries'
    )
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_entries'
    )
    period = models.ForeignKey(AccountingPeriod, on_delete=models.PROTECT, related_name='journal_entries')
    journal_number = models.CharField(max_length=30)
    journal_date = models.DateField()
    source = models.CharField(max_length=20, choices=JournalSource.choices, default=JournalSource.MANUAL)
    status = models.CharField(max_length=12, choices=JournalStatus.choices, default=JournalStatus.DRAFT)
    currency = models.ForeignKey(
        'core.Currency', on_delete=models.PROTECT, default='IDR', related_name='journal_entries'
    )
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    description = models.TextField(blank=True)
    reference = models.CharField(max_length=100, blank=True)

    submitted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    posted_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.TextField(blank=True)

    reversed_from = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='reversals'
    )
    is_reversed = models.BooleanField(
        default=False,
        help_text='Journal ini sudah di-reversal. Tetap POSTED (pasangannya membatalkan), tidak bisa di-void.',
    )

    class Meta:
        verbose_name = _('Journal Entry')
        verbose_name_plural = _('Journal Entries')
        ordering = ['-journal_date', '-id']
        unique_together = [('company', 'journal_number')]

    def __str__(self):
        return f'{self.journal_number} ({self.get_status_display()})'

    @property
    def total_debit(self):
        return sum((line.debit for line in self.lines.all()), Decimal('0'))

    @property
    def total_credit(self):
        return sum((line.credit for line in self.lines.all()), Decimal('0'))

    @property
    def is_balanced(self):
        return self.total_debit == self.total_credit

    # --- Workflow: DRAFT -> SUBMITTED -> APPROVED -> POSTED -> RECONCILED ---

    def submit(self, user=None):
        if self.status != JournalStatus.DRAFT:
            raise AccountingError('Hanya journal DRAFT yang bisa disubmit.')
        self.status = JournalStatus.SUBMITTED
        self.submitted_by = user
        self.submitted_at = timezone.now()
        self.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
        return self

    def approve(self, user=None):
        if self.status != JournalStatus.SUBMITTED:
            raise AccountingError('Hanya journal SUBMITTED yang bisa disetujui.')
        self.status = JournalStatus.APPROVED
        self.approved_by = user
        self.approved_at = timezone.now()
        self.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
        return self

    def post(self, user=None):
        """Rule 2 + 3 + 4 - immutable setelah posted, period harus OPEN, harus balance."""
        if self.status in (JournalStatus.POSTED, JournalStatus.RECONCILED):
            raise AccountingError('Journal sudah POSTED.')
        if self.is_reversed:
            raise AccountingError('Journal yang sudah di-reversal tidak bisa diposting ulang.')
        if self.status not in (JournalStatus.DRAFT, JournalStatus.APPROVED):
            raise AccountingError(f'Journal status {self.status} tidak bisa diposting.')
        if self.period.status == PeriodStatus.CLOSED:
            raise PeriodLockedError(
                f'Period {self.period.period_code} CLOSED. Reopen period dulu.'
            )
        if not self.lines.exists():
            raise AccountingError('Journal tanpa line tidak bisa diposting.')
        if not self.is_balanced:
            raise UnbalancedJournalError(
                f'Total debit {self.total_debit} != total credit {self.total_credit}.'
            )

        self.status = JournalStatus.POSTED
        self.posted_by = user
        self.posted_at = timezone.now()
        self.save(update_fields=['status', 'posted_by', 'posted_at', 'updated_at'])
        return self

    def void(self, user=None, reason=''):
        if self.status == JournalStatus.POSTED:
            raise AccountingError('Journal POSTED tidak bisa di-void. Gunakan reversal.')
        if self.is_reversed:
            raise AccountingError('Journal ini sudah di-reversal.')
        self.status = JournalStatus.VOID
        self.voided_by = user
        self.voided_at = timezone.now()
        self.void_reason = reason
        self.save(
            update_fields=['status', 'voided_by', 'voided_at', 'void_reason', 'updated_at']
        )
        return self


class JournalLine(models.Model):
    journal = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name='lines')
    line_no = models.PositiveSmallIntegerField(default=10)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='lines')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_lines'
    )
    department = models.ForeignKey(
        'core.Department', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_lines'
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_lines'
    )
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_lines'
    )
    customer = models.ForeignKey(
        'ar.Customer', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_lines'
    )
    supplier = models.ForeignKey(
        'ap.Supplier', null=True, blank=True, on_delete=models.PROTECT, related_name='journal_lines'
    )
    description = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=20, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    credit = models.DecimalField(max_digits=20, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    amount_base = models.DecimalField(
        max_digits=20, decimal_places=2, default=0,
        help_text='Nilai dalam base currency company',
    )

    class Meta:
        verbose_name = _('Journal Line')
        verbose_name_plural = _('Journal Lines')
        ordering = ['line_no', 'id']

    def __str__(self):
        return f'{self.account} D:{self.debit} C:{self.credit}'

    def clean(self):
        if self.debit and self.credit:
            raise ValidationError('Line tidak boleh memiliki debit dan credit sekaligus.')
        if not self.debit and not self.credit:
            raise ValidationError('Line harus memiliki debit atau credit.')

    def save(self, *args, **kwargs):
        if not self.amount_base:
            self.amount_base = (self.debit or self.credit) * self.journal.exchange_rate
        super().save(*args, **kwargs)


class JournalTemplate(models.Model):
    """PRD 11 - Recurring journal / accrual template."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='journal_templates')
    name = models.CharField(max_length=150)
    source = models.CharField(max_length=20, choices=JournalSource.choices, default=JournalSource.ACCRUAL)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Journal Template')
        verbose_name_plural = _('Journal Templates')

    def __str__(self):
        return self.name


class JournalTemplateLine(models.Model):
    template = models.ForeignKey(JournalTemplate, on_delete=models.CASCADE, related_name='lines')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='+')
    side = models.CharField(max_length=1, choices=NormalBalance.choices)
    is_percentage = models.BooleanField(default=False)
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)
    line_no = models.PositiveSmallIntegerField(default=10)

    class Meta:
        verbose_name = _('Journal Template Line')
        verbose_name_plural = _('Journal Template Lines')
        ordering = ['line_no']


class AccountMapping(TimeStampedModel):
    """PRD 9.2 - configurable accounting rules per company."""

    class Module(models.TextChoices):
        SALES = 'SALES', 'Sales'
        PURCHASE = 'PURCHASE', 'Purchase'
        AR = 'AR', 'Accounts Receivable'
        AP = 'AP', 'Accounts Payable'
        BANK = 'BANK', 'Bank'
        INVENTORY = 'INVENTORY', 'Inventory'
        ASSET = 'ASSET', 'Fixed Asset'
        TAX = 'TAX', 'Tax'
        INTERCOMPANY = 'INTERCOMPANY', 'Intercompany'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='account_mappings')
    module = models.CharField(max_length=20, choices=Module.choices)
    key = models.CharField(max_length=50, help_text='Contoh: AR_CONTROL, REVENUE, VAT_OUTPUT')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='+')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Account Mapping')
        verbose_name_plural = _('Account Mappings')
        unique_together = [('company', 'module', 'key')]

    def __str__(self):
        return f'{self.module}/{self.key} -> {self.account}'
