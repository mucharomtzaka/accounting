from decimal import Decimal

from django.db import models

from core.models import AuditMixin, TimeStampedModel
from django.utils.translation import gettext_lazy as _


class Customer(TimeStampedModel, AuditMixin):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='customers')
    customer_code = models.CharField(max_length=30)
    customer_name = models.CharField(max_length=255)
    legal_name = models.CharField(max_length=255, blank=True)
    npwp = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    city = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    contact_person = models.CharField(max_length=150, blank=True)
    payment_term_days = models.IntegerField(default=30)
    credit_limit = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    currency = models.ForeignKey(
        'core.Currency', on_delete=models.PROTECT, default='IDR', related_name='customers'
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.PROTECT, related_name='customers'
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Customer')
        verbose_name_plural = _('Customers')
        ordering = ['company', 'customer_code']
        unique_together = [('company', 'customer_code')]

    def __str__(self):
        return f'{self.customer_code} - {self.customer_name}'


class InvoiceStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    POSTED = 'POSTED', 'Posted'
    PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
    PAID = 'PAID', 'Paid'
    OVERDUE = 'OVERDUE', 'Overdue'
    VOID = 'VOID', 'Void'


class InvoiceType(models.TextChoices):
    INVOICE = 'INVOICE', 'Invoice'
    CREDIT_NOTE = 'CREDIT_NOTE', 'Credit Note'
    DEBIT_NOTE = 'DEBIT_NOTE', 'Debit Note'


class CustomerInvoice(TimeStampedModel, AuditMixin):
    """PRD 13 - Customer Invoice -> Dr AR / Cr Revenue, Cr VAT Output."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='customer_invoices')
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='customer_invoices')
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='invoices')
    department = models.ForeignKey(
        'core.Department', null=True, blank=True, on_delete=models.PROTECT, related_name='customer_invoices'
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.PROTECT, related_name='customer_invoices'
    )
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='customer_invoices'
    )
    invoice_number = models.CharField(max_length=30)
    invoice_type = models.CharField(max_length=15, choices=InvoiceType.choices, default=InvoiceType.INVOICE)
    invoice_date = models.DateField()
    due_date = models.DateField()
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR')
    exchange_rate = models.DecimalField(max_digits=20, decimal_places=8, default=1)
    payment_term = models.CharField(max_length=100, blank=True)
    subtotal = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    amount_paid = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='customer_invoices'
    )
    status = models.CharField(max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Customer Invoice')
        verbose_name_plural = _('Customer Invoices')
        ordering = ['-invoice_date', '-id']
        unique_together = [('company', 'invoice_number')]

    def __str__(self):
        return self.invoice_number

    @property
    def outstanding(self):
        return self.total - self.amount_paid

    @property
    def total_base(self):
        return self.total * self.exchange_rate

    @property
    def is_overdue(self):
        from django.utils import timezone

        return self.due_date < timezone.localdate() and self.outstanding > 0


class CustomerInvoiceLine(models.Model):
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.CASCADE, related_name='lines')
    line_no = models.PositiveSmallIntegerField(default=10)
    product = models.ForeignKey(
        'core.Product', null=True, blank=True, on_delete=models.PROTECT, related_name='invoice_lines'
    )
    account = models.ForeignKey(
        'gl.Account', on_delete=models.PROTECT, related_name='+', help_text='Revenue account'
    )
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=1)
    unit_price = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=7, decimal_places=2, default=0)
    tax_code = models.ForeignKey(
        'core.TaxCode', null=True, blank=True, on_delete=models.PROTECT, related_name='invoice_lines'
    )
    subtotal = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.PROTECT, related_name='invoice_lines'
    )

    class Meta:
        verbose_name = _('Customer Invoice Line')
        verbose_name_plural = _('Customer Invoice Lines')
        ordering = ['line_no', 'id']

    def __str__(self):
        return f'{self.line_no} {self.description}'


class PaymentStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    POSTED = 'POSTED', 'Posted'
    VOID = 'VOID', 'Void'


class CustomerPayment(TimeStampedModel, AuditMixin):
    """PRD 12.3 - Dr Bank / Cr AR."""

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='customer_payments')
    branch = models.ForeignKey('core.Branch', on_delete=models.PROTECT, related_name='customer_payments')
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='payments')
    bank_account = models.ForeignKey(
        'bank.BankAccount', on_delete=models.PROTECT, related_name='customer_payments'
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
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='customer_payments'
    )
    status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Customer Payment')
        verbose_name_plural = _('Customer Payments')
        ordering = ['-payment_date', '-id']
        unique_together = [('company', 'payment_number')]

    def __str__(self):
        return self.payment_number

    @property
    def unallocated(self):
        return self.amount - self.amount_allocated - self.bank_fee


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(CustomerPayment, on_delete=models.CASCADE, related_name='allocations')
    invoice = models.ForeignKey(CustomerInvoice, on_delete=models.PROTECT, related_name='allocations')
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Payment Allocation')
        verbose_name_plural = _('Payment Allocations')

    def __str__(self):
        return f'{self.payment} -> {self.invoice} {self.amount}'