from django.db import models

from core.models import TimeStampedModel
from django.utils.translation import gettext_lazy as _


class TaxType(models.TextChoices):
    OUTPUT = 'OUTPUT', 'Output VAT (PPN Keluaran)'
    INPUT = 'INPUT', 'Input VAT (PPN Masukan)'
    WITHHOLDING = 'WITHHOLDING', 'Withholding Tax (PPh)'


class TaxCode(TimeStampedModel):
    """PRD 21 - tax master, configurable per company."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='tax_codes')
    tax_code = models.CharField(max_length=20)
    name = models.CharField(max_length=150)
    tax_type = models.CharField(max_length=15, choices=TaxType.choices)
    rate = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    tax_account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Akun liability/receivable pajak'
    )
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Tax Code')

        verbose_name_plural = _('Tax Codes')

        ordering = ['company', 'tax_code']
        unique_together = [('company', 'tax_code')]

    def __str__(self):
        return f'{self.tax_code} - {self.name}'


class Product(TimeStampedModel):
    class Type(models.TextChoices):
        GOODS = 'GOODS', 'Goods / Product'
        SERVICE = 'SERVICE', 'Service'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='products')
    product_code = models.CharField(max_length=30)
    product_name = models.CharField(max_length=255)
    product_type = models.CharField(max_length=10, choices=Type.choices, default=Type.GOODS)
    uom = models.CharField(max_length=10, default='PCS')
    revenue_account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Account untuk revenue'
    )
    cogs_account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Account untuk COGS'
    )
    purchase_account = models.ForeignKey(
        'gl.Account', null=True, blank=True, on_delete=models.PROTECT, related_name='+',
        help_text='Account untuk pembelian (inventory/expense)',
    )
    price = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Product')

        verbose_name_plural = _('Products')

        ordering = ['company', 'product_code']
        unique_together = [('company', 'product_code')]

    def __str__(self):
        return f'{self.product_code} - {self.product_name}'