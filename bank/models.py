from django.db import models

from core.models import AuditMixin, TimeStampedModel
from django.utils.translation import gettext_lazy as _


class BankAccount(TimeStampedModel, AuditMixin):
    class Type(models.TextChoices):
        BANK = 'BANK', 'Bank'
        CASH = 'CASH', 'Cash'
        PETTY_CASH = 'PETTY_CASH', 'Petty Cash'

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='bank_accounts')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='bank_accounts'
    )
    gl_account = models.ForeignKey('gl.Account', on_delete=models.PROTECT, related_name='+')
    account_type = models.CharField(max_length=15, choices=Type.choices, default=Type.BANK)
    bank_name = models.CharField(max_length=150)
    account_number = models.CharField(max_length=50)
    account_name = models.CharField(max_length=150)
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR')
    is_default = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Bank Account')
        verbose_name_plural = _('Bank Accounts')
        ordering = ['company', 'bank_name']

    def __str__(self):
        return f'{self.bank_name} - {self.account_number}'


class BankTxStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    POSTED = 'POSTED', 'Posted'
    RECONCILED = 'RECONCILED', 'Reconciled'
    VOID = 'VOID', 'Void'


class BankTransaction(TimeStampedModel, AuditMixin):
    """Cash/Bank receipt, payment, transfer."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='bank_transactions')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='bank_transactions'
    )
    bank_account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='transactions')
    counterparty_bank_account = models.ForeignKey(
        BankAccount, null=True, blank=True, on_delete=models.PROTECT, related_name='incoming_transactions'
    )

    class TxType(models.TextChoices):
        RECEIPT = 'RECEIPT', 'Receipt'
        PAYMENT = 'PAYMENT', 'Payment'
        TRANSFER = 'TRANSFER', 'Transfer'
        CHARGE = 'CHARGE', 'Bank Charge'
        INTEREST = 'INTEREST', 'Interest'

    transaction_number = models.CharField(max_length=30)
    transaction_date = models.DateField()
    tx_type = models.CharField(max_length=20, choices=TxType.choices, default=TxType.RECEIPT)
    description = models.CharField(max_length=255)
    counterparty_name = models.CharField(max_length=150, blank=True)
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='bank_transactions'
    )
    reconciliation = models.ForeignKey(
        'bank.BankReconciliation', null=True, blank=True, on_delete=models.SET_NULL, related_name='transactions'
    )
    status = models.CharField(max_length=12, choices=BankTxStatus.choices, default=BankTxStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Bank Transaction')
        verbose_name_plural = _('Bank Transactions')
        ordering = ['-transaction_date', '-id']
        unique_together = [('company', 'transaction_number')]

    def __str__(self):
        return self.transaction_number


class BankStatementLine(TimeStampedModel):
    """PRD 16 - imported bank statement rows."""

    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name='statement_lines')
    reconciliation = models.ForeignKey(
        'bank.BankReconciliation', on_delete=models.CASCADE, related_name='statement_lines'
    )
    statement_date = models.DateField()
    value_date = models.DateField(null=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=100, blank=True)
    debit = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    matched_transaction = models.ForeignKey(
        BankTransaction, null=True, blank=True, on_delete=models.SET_NULL, related_name='matched_lines'
    )
    match_type = models.CharField(max_length=20, default='UNMATCHED',
                                  choices=[('UNMATCHED', 'Unmatched'), ('AUTO', 'Auto'),
                                           ('MANUAL', 'Manual'), ('PARTIAL', 'Partial')])

    class Meta:
        verbose_name = _('Bank Statement Line')
        verbose_name_plural = _('Bank Statement Lines')
        ordering = ['statement_date', 'id']

    @property
    def amount(self):
        return self.credit - self.debit

    @property
    def is_matched(self):
        return self.matched_transaction_id is not None


class BankReconciliation(TimeStampedModel, AuditMixin):
    class Status(models.TextChoices):
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='bank_reconciliations')
    bank_account = models.ForeignKey(BankAccount, on_delete=models.CASCADE, related_name='reconciliations')
    reconciliation_name = models.CharField(max_length=100)
    statement_date = models.DateField()
    start_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    statement_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    book_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.IN_PROGRESS)
    completed_by = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Bank Reconciliation')
        verbose_name_plural = _('Bank Reconciliations')
        ordering = ['-statement_date']

    def __str__(self):
        return self.reconciliation_name

    @property
    def difference(self):
        return self.statement_balance - self.book_balance

    @property
    def unmatched_count(self):
        return self.statement_lines.filter(matched_transaction__isnull=True).count()