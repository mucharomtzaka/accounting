"""PRD 20 - Fixed Asset."""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from core.models import AuditMixin, TimeStampedModel
from django.utils.translation import gettext_lazy as _


class AssetStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    ACTIVE = 'ACTIVE', 'Active'
    FULLY_DEPRECIATED = 'FULLY_DEPRECIATED', 'Fully Depreciated'
    DISPOSED = 'DISPOSED', 'Disposed'


class DepreciationMethod(models.TextChoices):
    STRAIGHT_LINE = 'STRAIGHT_LINE', 'Straight Line'


class AssetCategory(TimeStampedModel):
    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='asset_categories')
    category_code = models.CharField(max_length=30)
    category_name = models.CharField(max_length=150)
    asset_account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Aset Tetap - Perolehan'
    )
    accumulated_account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Akumulasi Penyusutan'
    )
    depreciation_account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Beban Penyusutan'
    )
    disposal_gain_account = models.ForeignKey(
        'gl.Account', null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    disposal_loss_account = models.ForeignKey(
        'gl.Account', null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )
    useful_life_years = models.PositiveIntegerField(default=5)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Asset Category')
        verbose_name_plural = _('Asset Categories')
        ordering = ['company', 'category_code']
        unique_together = [('company', 'category_code')]

    def __str__(self):
        return f'{self.category_code} - {self.category_name}'


class FixedAsset(TimeStampedModel, AuditMixin):
    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='fixed_assets')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='fixed_assets'
    )
    department = models.ForeignKey(
        'core.Department', null=True, blank=True, on_delete=models.PROTECT, related_name='fixed_assets'
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.PROTECT, related_name='fixed_assets'
    )
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='fixed_assets'
    )
    asset_code = models.CharField(max_length=50)
    asset_name = models.CharField(max_length=255)
    asset_category = models.ForeignKey(AssetCategory, on_delete=models.PROTECT, related_name='assets')
    serial_number = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=255, blank=True)
    acquisition_date = models.DateField()
    acquisition_cost = models.DecimalField(
        max_digits=20, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    residual_value = models.DecimalField(
        max_digits=20, decimal_places=2, default=0, validators=[MinValueValidator(0)]
    )
    useful_life_years = models.PositiveIntegerField(default=5)
    depreciation_method = models.CharField(
        max_length=20, choices=DepreciationMethod.choices, default=DepreciationMethod.STRAIGHT_LINE
    )
    start_depreciation_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=AssetStatus.choices, default=AssetStatus.DRAFT)
    accumulated_depreciation = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    net_book_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    last_depreciation_date = models.DateField(null=True, blank=True)
    acquisition_journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='asset_acquisitions'
    )
    disposal_journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='asset_disposals'
    )

    class Meta:
        verbose_name = _('Fixed Asset')
        verbose_name_plural = _('Fixed Assets')
        ordering = ['company', 'asset_code']
        unique_together = [('company', 'asset_code')]

    def __str__(self):
        return f'{self.asset_code} - {self.asset_name}'

    @property
    def depreciable_base(self):
        base = self.acquisition_cost - self.residual_value
        return base if base > 0 else Decimal('0')

    @property
    def annual_depreciation(self):
        if self.useful_life_years <= 0:
            return Decimal('0')
        return (self.depreciable_base / Decimal(self.useful_life_years)).quantize(Decimal('0.01'))

    @property
    def is_fully_depreciated(self):
        return self.accumulated_depreciation >= self.depreciable_base


class AssetDepreciation(TimeStampedModel):
    """PRD 20 - satu baris per asset per accounting period."""

    asset = models.ForeignKey(FixedAsset, on_delete=models.CASCADE, related_name='depreciations')
    period = models.ForeignKey('gl.AccountingPeriod', on_delete=models.PROTECT, related_name='depreciations')
    depreciation_date = models.DateField()
    depreciation_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    accumulated_before = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    accumulated_after = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    net_book_value_before = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    net_book_value_after = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='depreciations'
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _('Asset Depreciation')
        verbose_name_plural = _('Asset Depreciations')
        ordering = ['-depreciation_date', '-id']
        unique_together = [('asset', 'period')]

    def __str__(self):
        return f'{self.asset.asset_code} {self.depreciation_date} {self.depreciation_amount}'


class AssetTransaction(TimeStampedModel):
    asset = models.ForeignKey(FixedAsset, on_delete=models.CASCADE, related_name='transactions')
    tx_type = models.CharField(max_length=15, choices=[
        ('ACQUISITION', 'Acquisition'),
        ('DEPRECIATION', 'Depreciation'),
        ('TRANSFER', 'Transfer'),
        ('REVALUATION', 'Revaluation'),
        ('IMPAIRMENT', 'Impairment'),
        ('DISPOSAL', 'Disposal'),
    ])
    transaction_date = models.DateField()
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    description = models.CharField(max_length=255, blank=True)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='asset_transactions'
    )

    class Meta:
        verbose_name = _('Asset Transaction')
        verbose_name_plural = _('Asset Transactions')
        ordering = ['-transaction_date', '-id']

    def __str__(self):
        return f'{self.asset.asset_code} {self.tx_type} {self.amount}'