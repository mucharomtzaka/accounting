from django.db import models

from core.models import AuditMixin, TimeStampedModel
from django.utils.translation import gettext_lazy as _


class Supplier(TimeStampedModel, AuditMixin):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='suppliers')
    supplier_code = models.CharField(max_length=30)
    supplier_name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    npwp = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    payment_term_days = models.IntegerField(default=30)
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR', related_name='suppliers')
    is_intercompany = models.BooleanField(default=False, help_text='Supplier dari company lain dalam group')
    intercompany_company = models.ForeignKey(
        'core.Company', null=True, blank=True, on_delete=models.PROTECT, related_name='intercompany_suppliers'
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Supplier')
        verbose_name_plural = _('Suppliers')
        ordering = ['company', 'supplier_code']
        unique_together = [('company', 'supplier_code')]

    def __str__(self):
        return f'{self.supplier_code} - {self.supplier_name}'


class BillStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    POSTED = 'POSTED', 'Posted'
    PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
    PAID = 'PAID', 'Paid'
    OVERDUE = 'OVERDUE', 'Overdue'
    VOID = 'VOID', 'Void'


class BillType(models.TextChoices):
    BILL = 'BILL', 'Vendor Bill'
    CREDIT_NOTE = 'CREDIT_NOTE', 'Credit Note'
    DEBIT_NOTE = 'DEBIT_NOTE', 'Debit Note'


class SupplierBill(TimeStampedModel, AuditMixin):
    """PRD 14 - Vendor Bill -> Dr Inventory/Expense, Dr Input VAT / Cr AP."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='supplier_bills')
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='supplier_bills')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='bills')
    department = models.ForeignKey(
        'core.Department', null=True, blank=True, on_delete=models.PROTECT, related_name='supplier_bills'
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.PROTECT, related_name='supplier_bills'
    )
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='supplier_bills'
    )
    bill_number = models.CharField(max_length=30)
    supplier_reference = models.CharField(max_length=100, blank=True)
    bill_type = models.CharField(max_length=15, choices=BillType.choices, default=BillType.BILL)
    bill_date = models.DateField()
    due_date = models.DateField()
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    subtotal = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    withholding_tax = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='supplier_bills'
    )
    status = models.CharField(max_length=20, choices=BillStatus.choices, default=BillStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Supplier Bill')
        verbose_name_plural = _('Supplier Bills')
        ordering = ['-bill_date', '-id']
        unique_together = [('company', 'bill_number')]

    def __str__(self):
        return self.bill_number

    @property
    def outstanding(self):
        return self.total - self.amount_paid

    @property
    def is_overdue(self):
        from django.utils import timezone

        return self.due_date < timezone.localdate() and self.outstanding > 0


class SupplierBillLine(models.Model):
    bill = models.ForeignKey(SupplierBill, on_delete=models.CASCADE, related_name='lines')
    line_no = models.PositiveSmallIntegerField(default=10)
    product = models.ForeignKey(
        'core.Product', null=True, blank=True, on_delete=models.PROTECT, related_name='bill_lines'
    )
    account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Expense / Inventory account'
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=1)
    unit_price = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    tax_code = models.ForeignKey(
        'core.TaxCode', null=True, blank=True, on_delete=models.PROTECT, related_name='bill_lines'
    )
    subtotal = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='bill_lines'
    )
    po_number = models.CharField(max_length=50, blank=True, help_text='Three-way match')
    receipt_number = models.CharField(max_length=50, blank=True, help_text='Three-way match')

    class Meta:
        verbose_name = _('Supplier Bill Line')
        verbose_name_plural = _('Supplier Bill Lines')
        ordering = ['line_no', 'id']

    def __str__(self):
        return f'{self.line_no} {self.description}'


class SupplierPayment(TimeStampedModel, AuditMixin):
    """PRD 12.4 - Dr AP / Cr Bank."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='supplier_payments')
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='supplier_payments')
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name='payments')
    bank_account = models.ForeignKey(
        'bank.BankAccount', on_delete=models.PROTECT, related_name='supplier_payments'
    )
    payment_number = models.CharField(max_length=30)
    payment_date = models.DateField()
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    amount_allocated = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    bank_fee = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    payment_method = models.CharField(max_length=30, blank=True)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='supplier_payments'
    )
    status = models.CharField(max_length=10, default='DRAFT')
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Supplier Payment')
        verbose_name_plural = _('Supplier Payments')
        ordering = ['-payment_date', '-id']
        unique_together = [('company', 'payment_number')]

    def __str__(self):
        return self.payment_number

    @property
    def unallocated(self):
        return self.amount - self.amount_allocated - self.bank_fee


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(SupplierPayment, on_delete=models.CASCADE, related_name='allocations')
    bill = models.ForeignKey(SupplierBill, on_delete=models.PROTECT, related_name='allocations')
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Supplier Payment Allocation')
        verbose_name_plural = _('Supplier Payment Allocations')

    def __str__(self):
        return f'{self.payment} -> {self.bill} {self.amount}'