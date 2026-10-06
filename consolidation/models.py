"""PRD 24-25 - Intercompany & Consolidation."""
from django.db import models

from core.models import TimeStampedModel
from django.utils.translation import gettext_lazy as _


class IntercompanyType(models.TextChoices):
    SALES = 'SALES', 'Intercompany Sales'
    PURCHASE = 'PURCHASE', 'Intercompany Purchase'
    EXPENSE = 'EXPENSE', 'Intercompany Expense'
    LOAN = 'LOAN', 'Intercompany Loan'
    TRANSFER = 'TRANSFER', 'Intercompany Transfer'


class IntercompanyStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    POSTED = 'POSTED', 'Posted'
    MATCHED = 'MATCHED', 'Matched'
    UNMATCHED = 'UNMATCHED', 'Unmatched'
    ELIMINATED = 'ELIMINATED', 'Eliminated'


class IntercompanyTransaction(TimeStampedModel):
    """PRD 24.1 - source company + target company wajib (Rule 6)."""

    group = models.ForeignKey('core.Group', on_delete=models.CASCADE, related_name='intercompany_transactions')
    source_company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE, related_name='ic_outgoing'
    )
    target_company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE, related_name='ic_incoming'
    )
    transaction_number = models.CharField(max_length=40)
    transaction_date = models.DateField()
    tx_type = models.CharField(max_length=12, choices=IntercompanyType.choices)
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    description = models.TextField(blank=True)
    source_document = models.CharField(max_length=100, blank=True, help_text='Inv/Bill/PO asal')
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='intercompany_transactions',
    )
    status = models.CharField(max_length=12, choices=IntercompanyStatus.choices, default=IntercompanyStatus.DRAFT)
    eliminated_in = models.ForeignKey(
        'ConsolidationRun', null=True, blank=True, on_delete=models.SET_NULL, related_name='intercompany_eliminated'
    )

    class Meta:
        verbose_name = _('Intercompany Transaction')
        verbose_name_plural = _('Intercompany Transactions')
        ordering = ['-transaction_date', '-id']
        unique_together = [('source_company', 'transaction_number')]

    def __str__(self):
        return f'{self.transaction_number} ({self.get_tx_type_display()})'

    @property
    def matched(self):
        return self.status == IntercompanyStatus.MATCHED

    @property
    def amount_base(self):
        return self.amount * self.exchange_rate


class ConsolidationRun(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'
        APPROVED = 'APPROVED', 'Approved'

    group = models.ForeignKey('core.Group', on_delete=models.CASCADE, related_name='consolidation_runs')
    run_name = models.CharField(max_length=150)
    period_from = models.DateField()
    period_to = models.DateField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT)
    eliminated_count = models.PositiveIntegerField(default=0)
    eliminated_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Consolidation Run')
        verbose_name_plural = _('Consolidation Runs')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.run_name} ({self.get_status_display()})'


class ConsolidationAdjustment(TimeStampedModel):
    run = models.ForeignKey(ConsolidationRun, on_delete=models.CASCADE, related_name='adjustments')
    description = models.CharField(max_length=255)
    account = models.ForeignKey('gl.Account', on_delete=models.PROTECT, related_name='+')
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)

    class Meta:
        verbose_name = _('Consolidation Adjustment')
        verbose_name_plural = _('Consolidation Adjustments')

    def __str__(self):
        return f'{self.run.run_name} - {self.description}'