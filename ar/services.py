from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from gl.engine import AccountingError, JournalBuilder, get_account
from gl.models import JournalSource

from .models import CustomerInvoice, CustomerPayment, InvoiceStatus, PaymentAllocation


def recalc_invoice_totals(invoice):
    subtotal = Decimal('0')
    tax = Decimal('0')
    for line in invoice.lines.all():
        line.subtotal = line.quantity * line.unit_price - line.discount
        line.tax_amount = (line.subtotal * line.tax_rate / Decimal('100')).quantize(Decimal('0.01'))
        line.total = line.subtotal + line.tax_amount
        line.save(update_fields=['subtotal', 'tax_amount', 'total'])
        subtotal += line.subtotal
        tax += line.tax_amount
    invoice.subtotal = subtotal
    invoice.tax = tax
    invoice.total = subtotal + tax - invoice.discount
    invoice.save(update_fields=['subtotal', 'tax', 'total'])
    return invoice


def post_invoice(invoice, user=None):
    """PRD 12.1 - Dr AR / Cr Revenue / Cr VAT Output."""
    if invoice.status != InvoiceStatus.DRAFT:
        raise ValidationError(f'Invoice {invoice.invoice_number} sudah diposting.')
    if not invoice.lines.exists():
        raise ValidationError('Invoice tanpa line tidak bisa diposting.')

    ar_account = get_account(invoice.company, 'AR', 'AR_CONTROL')
    vat_account = get_account(invoice.company, 'TAX', 'VAT_OUTPUT', required=False)

    revenue_by_account = {}
    tax_by_code = {}
    for line in invoice.lines.all():
        revenue_by_account[line.account_id] = (
            revenue_by_account.get(line.account_id, Decimal('0')) + line.subtotal
        )
        if line.tax_code_id:
            tax_by_code[line.tax_code_id] = tax_by_code.get(line.tax_code_id, Decimal('0')) + line.tax_amount

    builder = JournalBuilder(
        invoice.company, invoice.invoice_date,
        source=JournalSource.SALES_INVOICE,
        description=f'Invoice {invoice.invoice_number} - {invoice.customer.customer_name}',
        currency=invoice.currency_id, exchange_rate=invoice.exchange_rate,
        branch=invoice.branch, department=invoice.department,
        cost_center=invoice.cost_center, project=invoice.project,
        reference=invoice.invoice_number, user=user,
    )
    builder.debit(ar_account, invoice.total, f'Piutang - {invoice.customer.customer_name}',
                  customer=invoice.customer)

    for account_id, amount in revenue_by_account.items():
        account = invoice.lines.filter(account_id=account_id).first().account
        builder.credit(account, amount, account.account_name, customer=invoice.customer)

    if invoice.tax:
        for tax_code_id, amount in tax_by_code.items():
            code = invoice.lines.filter(tax_code_id=tax_code_id).first().tax_code
            builder.credit(code.tax_account, amount, code.name, project=invoice.project)

    if invoice.discount:
        builder.debit(
            get_account(invoice.company, 'SALES', 'DISCOUNT', required=False) or ar_account,
            invoice.discount, 'Diskon',
        )

    journal = builder.save(auto_post=True)
    CustomerInvoice.objects.filter(pk=invoice.pk).update(
        status=InvoiceStatus.POSTED, journal=journal
    )
    invoice.status = InvoiceStatus.POSTED
    invoice.journal = journal
    return journal


@transaction.atomic
def allocate_payment(payment, invoice, amount):
    """PRD 13.2 - payment allocation."""
    amount = Decimal(amount)
    if amount <= 0:
        raise ValidationError('Alokasi harus lebih dari 0.')
    if invoice.company_id != payment.company_id:
        raise ValidationError('Invoice dan payment berbeda company.')
    if amount > invoice.outstanding:
        raise ValidationError(
            f'Alokasi melebihi sisa invoice {invoice.invoice_number} '
            f'({invoice.outstanding}).'
        )
    if amount > payment.unallocated:
        raise ValidationError(f'Alokasi melebihi sisa payment ({payment.unallocated}).')

    PaymentAllocation.objects.create(payment=payment, invoice=invoice, amount=amount)
    payment.amount_allocated += amount
    payment.save(update_fields=['amount_allocated'])

    invoice.amount_paid += amount
    invoice.save(update_fields=['amount_paid'])
    refresh_invoice_status(invoice)
    return invoice


def refresh_invoice_status(invoice):
    if invoice.outstanding <= 0:
        invoice.status = InvoiceStatus.PAID
    elif invoice.amount_paid > 0:
        invoice.status = InvoiceStatus.PARTIALLY_PAID
    elif invoice.is_overdue:
        invoice.status = InvoiceStatus.OVERDUE
    else:
        invoice.status = InvoiceStatus.POSTED
    invoice.save(update_fields=['status'])
    return invoice


def post_payment(payment, user=None):
    """PRD 12.3 - Dr Bank / Cr AR (+ fee ke expense)."""
    if payment.status != 'DRAFT':
        raise ValidationError(f'Payment {payment.payment_number} sudah diposting.')

    ar_account = get_account(payment.company, 'AR', 'AR_CONTROL')
    bank_gl = payment.bank_account.gl_account

    builder = JournalBuilder(
        payment.company, payment.payment_date,
        source=JournalSource.CUSTOMER_PAYMENT,
        description=f'Penerimaan - {payment.customer.customer_name}',
        currency=payment.currency_id, exchange_rate=payment.exchange_rate,
        branch=payment.branch, reference=payment.payment_number, user=user,
    )
    builder.debit(bank_gl, payment.amount, 'Penerimaan bank')
    builder.credit(ar_account, payment.amount_allocated, f'Piutang - {payment.customer.customer_name}',
                   customer=payment.customer)
    if payment.bank_fee:
        fee_account = get_account(payment.company, 'BANK', 'BANK_CHARGE', required=False)
        if fee_account:
            builder.debit(fee_account, payment.bank_fee, 'Biaya bank')
    if payment.amount - payment.amount_allocated - payment.bank_fee > 0:
        builder.credit(
            get_account(payment.company, 'AR', 'AR_UNBILLED', required=False) or ar_account,
            payment.amount - payment.amount_allocated - payment.bank_fee, 'Deposit belum dialokasi',
        )

    journal = builder.save(auto_post=True)
    CustomerPayment.objects.filter(pk=payment.pk).update(status='POSTED', journal=journal)
    payment.status = 'POSTED'
    payment.journal = journal
    return journal


def next_invoice_number(company, invoice_type):
    prefix = {'INVOICE': 'INV', 'CREDIT_NOTE': 'CN', 'DEBIT_NOTE': 'DN'}[invoice_type]
    from django.utils import timezone

    code = f'{prefix}/{timezone.localdate():%Y%m}/'
    last = (
        CustomerInvoice.objects.filter(company=company, invoice_number__startswith=code)
        .order_by('-invoice_number').values_list('invoice_number', flat=True).first()
    )
    seq = int(last.rsplit('/', 1)[1]) + 1 if last else 1
    return f'{code}{seq:04d}'