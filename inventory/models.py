"""PRD 17 - Inventory Accounting."""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from core.models import AuditMixin, TimeStampedModel
from django.utils.translation import gettext_lazy as _


class CostingMethod(models.TextChoices):
    MOVING_AVERAGE = 'MOVING_AVERAGE', 'Moving Average'
    FIFO = 'FIFO', 'FIFO'


class Warehouse(TimeStampedModel, AuditMixin):
    """Gudang: master data per branch. Sebelumnya `Inventory.warehouse` cuma
    teks bebas, jadi tidak bisa dijadikan acuan report/stock per gudang."""

    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='warehouses')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='warehouses'
    )
    warehouse_code = models.CharField(max_length=20)
    warehouse_name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Warehouse')
        verbose_name_plural = _('Warehouses')
        ordering = ['company', 'warehouse_code']
        unique_together = [('company', 'warehouse_code')]

    def __str__(self):
        return f'{self.warehouse_code} - {self.warehouse_name}'


class Inventory(TimeStampedModel, AuditMixin):
    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='inventories')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='inventories'
    )
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='inventories'
    )
    product = models.OneToOneField('core.Product', on_delete=models.CASCADE, related_name='inventory')
    quantity_on_hand = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    quantity_reserved = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    average_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    last_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    valuation = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    costing_method = models.CharField(
        max_length=20, choices=CostingMethod.choices, default=CostingMethod.MOVING_AVERAGE
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Inventory')
        verbose_name_plural = _('Inventories')
        ordering = ['company', 'product__product_code']

    def __str__(self):
        return f'{self.product.product_code} - QOH {self.quantity_on_hand}'

    @property
    def available(self):
        return self.quantity_on_hand - self.quantity_reserved

    @property
    def revaluation_delta(self):
        return self.valuation - self.total_cost


class InventoryTransaction(TimeStampedModel, AuditMixin):
    class TxType(models.TextChoices):
        GR = 'GR', 'Goods Receipt'
        GI = 'GI', 'Goods Issue'
        ADJUSTMENT_IN = 'ADJUSTMENT_IN', 'Stock Adjustment In'
        ADJUSTMENT_OUT = 'ADJUSTMENT_OUT', 'Stock Adjustment Out'
        RETURN_FROM_CUSTOMER = 'RETURN_FROM_CUSTOMER', 'Sales Return'
        RETURN_TO_SUPPLIER = 'RETURN_TO_SUPPLIER', 'Purchase Return'

    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name='transactions')
    product = models.ForeignKey('core.Product', on_delete=models.CASCADE, related_name='inventory_transactions')
    tx_type = models.CharField(max_length=20, choices=TxType.choices)
    transaction_date = models.DateField()
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    reference = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=255, blank=True)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='inventory_transactions',
    )
    is_posted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Inventory Transaction')
        verbose_name_plural = _('Inventory Transactions')
        ordering = ['-transaction_date', '-id']

    def __str__(self):
        return f'{self.product.product_code} {self.tx_type} {self.quantity}'

    @property
    def is_in(self):
        return self.tx_type in (
            'GR', 'ADJUSTMENT_IN', 'RETURN_FROM_CUSTOMER',
        )


class InventoryCost(TimeStampedModel):
    """PRD 17 - FIFO cost layer / moving average snapshot per transaksi."""

    inventory = models.ForeignKey(Inventory, on_delete=models.CASCADE, related_name='cost_layers')
    transaction = models.ForeignKey(
        InventoryTransaction, on_delete=models.CASCADE, related_name='cost_layers'
    )
    quantity_in = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    quantity_out = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    quantity_balance = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_cost_balance = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    method = models.CharField(max_length=20, choices=CostingMethod.choices)

    class Meta:
        verbose_name = _('Inventory Cost Layer')
        verbose_name_plural = _('Inventory Cost Layers')
        ordering = ['id']

    def __str__(self):
        return f'{self.inventory.product.product_code} bal={self.quantity_balance}'


class StockOpname(TimeStampedModel, AuditMixin):
    """PRD 17 - stock opname (sensus fisik) per gudang.

    Menyimpan hasil hitung fisik vs qty sistem sebagai dokumen, bukan langsung
    adjustment satu-satu. Variance baru masuk ke Inventory + GL saat di-post.
    """

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        COUNTED = 'COUNTED', 'Counted'
        POSTED = 'POSTED', 'Posted'
        CANCELLED = 'CANCELLED', 'Cancelled'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='stock_opnames')
    warehouse = models.ForeignKey(
        Warehouse, null=True, blank=True, on_delete=models.PROTECT, related_name='stock_opnames'
    )
    opname_number = models.CharField(max_length=30)
    opname_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)
    counted_by = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    counted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    posted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Stock Opname')
        verbose_name_plural = _('Stock Opnames')
        ordering = ['-opname_date', '-id']
        unique_together = [('company', 'opname_number')]

    def __str__(self):
        return f'{self.opname_number} ({self.get_status_display()})'

    @property
    def is_editable(self):
        return self.status in (self.Status.DRAFT, self.Status.COUNTED)

    @property
    def variance_total(self):
        return sum((line.variance for line in self.lines.all()), Decimal('0'))


class StockOpnameLine(TimeStampedModel):
    opname = models.ForeignKey(StockOpname, on_delete=models.CASCADE, related_name='lines')
    inventory = models.ForeignKey(Inventory, on_delete=models.PROTECT, related_name='opname_lines')
    system_quantity = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    counted_quantity = models.DecimalField(max_digits=18, decimal_places=4, default=0)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        verbose_name = _('Stock Opname Line')
        verbose_name_plural = _('Stock Opname Lines')
        ordering = ['id']
        unique_together = [('opname', 'inventory')]

    def __str__(self):
        return f'{self.inventory.product.product_code}: {self.counted_quantity}'

    @property
    def variance(self):
        """Selisih fisik - sistem. Positif berarti stok fisik lebih banyak."""
        return self.counted_quantity - self.system_quantity

    @property
    def variance_value(self):
        return (self.variance * self.unit_cost).quantize(Decimal('0.01'))