from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from gl.engine import JournalBuilder, get_account
from gl.models import JournalSource

from .models import BillStatus, PaymentAllocation, SupplierBill, SupplierPayment


def recalc_bill_totals(bill):
    subtotal = Decimal('0')
    tax = Decimal('0')
    for line in bill.lines.all():
        line.subtotal = line.quantity * line.unit_price - line.discount
        line.tax_amount = (line.subtotal * line.tax_rate / Decimal('100')).quantize(Decimal('0.01'))
        line.total = line.subtotal + line.tax_amount
        line.save(update_fields=['subtotal', 'tax_amount', 'total'])
        subtotal += line.subtotal
        tax += line.tax_amount
    bill.subtotal = subtotal
    bill.tax = tax
    bill.total = subtotal + tax - bill.discount - bill.withholding_tax
    bill.save(update_fields=['subtotal', 'tax', 'total'])
    return bill


def post_bill(bill, user=None):
    """PRD 12.2 - Dr Expense/Inventory, Dr Input VAT / Cr AP."""
    if bill.status != BillStatus.DRAFT:
        raise ValidationError(f'Bill {bill.bill_number} sudah diposting.')
    if not bill.lines.exists():
        raise ValidationError('Bill tanpa line tidak bisa diposting.')

    ap_account = get_account(bill.company, 'AP', 'AP_CONTROL')
    expense_by_account = {}
    for line in bill.lines.all():
        expense_by_account[line.account_id] = expense_by_account.get(line.account_id, Decimal('0')) + line.subtotal

    builder = JournalBuilder(
        bill.company, bill.bill_date,
        source=JournalSource.PURCHASE_INVOICE,
        description=f'Bill {bill.bill_number} - {bill.supplier.supplier_name}',
        currency=bill.currency_id, exchange_rate=bill.exchange_rate,
        branch=bill.branch, department=bill.department,
        cost_center=bill.cost_center, project=bill.project,
        reference=bill.bill_number, user=user,
    )
    for account_id, amount in expense_by_account.items():
        account = bill.lines.filter(account_id=account_id).first().account
        builder.debit(account, amount, account.account_name, supplier=bill.supplier)

    if bill.tax:
        for code in {line.tax_code_id for line in bill.lines.all() if line.tax_code_id}:
            amount = sum(
                (l.tax_amount for l in bill.lines.all() if l.tax_code_id == code), Decimal('0')
            )
            tax_code = bill.lines.filter(tax_code_id=code).first().tax_code
            builder.debit(tax_code.tax_account, amount, tax_code.name)

    if bill.withholding_tax:
        builder.credit(
            get_account(bill.company, 'TAX', 'WITHHOLDING_PAYABLE', required=False)
            or get_account(bill.company, 'AP', 'AP_CONTROL'),
            bill.withholding_tax, 'Pajak-potong',
        )

    builder.credit(ap_account, bill.total, f'Utang - {bill.supplier.supplier_name}', supplier=bill.supplier)

    journal = builder.save(auto_post=True)
    SupplierBill.objects.filter(pk=bill.pk).update(status=BillStatus.POSTED, journal=journal)
    bill.status = BillStatus.POSTED
    bill.journal = journal
    return journal


@transaction.atomic
def allocate_payment(payment, bill, amount):
    amount = Decimal(amount)
    if amount <= 0:
        raise ValidationError('Alokasi harus lebih dari 0.')
    if bill.company_id != payment.company_id:
        raise ValidationError('Bill dan payment berbeda company.')
    if amount > bill.outstanding:
        raise ValidationError(f'Alokasi melebihi sisa bill {bill.bill_number} ({bill.outstanding}).')
    if amount > payment.unallocated:
        raise ValidationError(f'Alokasi melebihi sisa payment ({payment.unallocated}).')

    PaymentAllocation.objects.create(payment=payment, bill=bill, amount=amount)
    payment.amount_allocated += amount
    payment.save(update_fields=['amount_allocated'])

    bill.amount_paid += amount
    bill.save(update_fields=['amount_paid'])
    refresh_bill_status(bill)
    return bill


def refresh_bill_status(bill):
    if bill.outstanding <= 0:
        bill.status = BillStatus.PAID
    elif bill.amount_paid > 0:
        bill.status = BillStatus.PARTIALLY_PAID
    elif bill.is_overdue:
        bill.status = BillStatus.OVERDUE
    else:
        bill.status = BillStatus.POSTED
    bill.save(update_fields=['status'])
    return bill


def post_payment(payment, user=None):
    """PRD 12.4 - Dr AP / Cr Bank."""
    if payment.status != 'DRAFT':
        raise ValidationError(f'Payment {payment.payment_number} sudah diposting.')

    ap_account = get_account(payment.company, 'AP', 'AP_CONTROL')
    bank_gl = payment.bank_account.gl_account

    builder = JournalBuilder(
        payment.company, payment.payment_date,
        source=JournalSource.SUPPLIER_PAYMENT,
        description=f'Pembayaran - {payment.supplier.supplier_name}',
        currency=payment.currency_id, exchange_rate=payment.exchange_rate,
        branch=payment.branch, reference=payment.payment_number, user=user,
    )
    builder.debit(ap_account, payment.amount_allocated,
                  f'Utang - {payment.supplier.supplier_name}', supplier=payment.supplier)
    builder.credit(bank_gl, payment.amount, 'Pembayaran bank')
    if payment.bank_fee:
        fee_account = get_account(payment.company, 'BANK', 'BANK_CHARGE', required=False)
        if fee_account:
            builder.debit(fee_account, payment.bank_fee, 'Biaya bank')
    if payment.amount - payment.amount_allocated - payment.bank_fee > 0:
        builder.debit(
            get_account(payment.company, 'AP', 'AP_UNBILLED', required=False) or ap_account,
            payment.amount - payment.amount_allocated - payment.bank_fee, 'Advance / sisa',
        )

    journal = builder.save(auto_post=True)
    SupplierPayment.objects.filter(pk=payment.pk).update(status='POSTED', journal=journal)
    payment.status = 'POSTED'
    payment.journal = journal
    return journal


def three_way_match(bill):
    """PRD 14.1 - PO + Receipt + Invoice per line.

    PONTAIL: hanya validasi referensi PO/receipt. Pencocokan quantity & harga
    butuh model PurchaseOrder/GoodsReceipt (belum ada di MVP) - tambahkan
    ketika modul procurement dibangun.
    """
    exceptions = []
    for line in bill.lines.all():
        if not line.po_number or not line.receipt_number:
            exceptions.append({
                'line': line, 'type': 'MISSING_REF',
                'message': f'Line {line.line_no}: PO atau nomor receipt belum diisi.',
            })
    return exceptions


def next_bill_number(company, bill_type):
    from django.utils import timezone

    prefix = {'BILL': 'BILL', 'CREDIT_NOTE': 'PCN', 'DEBIT_NOTE': 'PDN'}[bill_type]
    code = f'{prefix}/{timezone.localdate():%Y%m}/'
    last = (
        SupplierBill.objects.filter(company=company, bill_number__startswith=code)
        .order_by('-bill_number').values_list('bill_number', flat=True).first()
    )
    seq = int(last.rsplit('/', 1)[1]) + 1 if last else 1
    return f'{code}{seq:04d}'