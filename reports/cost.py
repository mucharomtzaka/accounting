"""PRD 19 - Cost Accounting & PRD 27 - Management Reports.

Profitability dihitung dari General Ledger (journal POSTED), dikelompokkan
berdasarkan dimensi yang ada di JournalLine. Product profitability tidak bisa
dari GL karena JournalLine tidak punya field product - diambil dari
CustomerInvoiceLine (revenue) dan COGS per produk.
"""
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum

from gl.models import AccountCategory, JournalLine, JournalStatus

POSTED = [JournalStatus.POSTED, JournalStatus.RECONCILED]

DIMENSIONS = {
    'customer': ('customer', 'customer__customer_name'),
    'supplier': ('supplier', 'supplier__supplier_name'),
    'branch': ('branch', 'branch__branch_name'),
    'department': ('department', 'department__department_name'),
    'cost_center': ('cost_center', 'cost_center__cost_center_name'),
    'project': ('project', 'project__project_name'),
}


def _lines(companies, start=None, end=None):
    qs = JournalLine.objects.filter(
        journal__company__in=companies, journal__status__in=POSTED
    )
    if start:
        qs = qs.filter(journal__journal_date__gte=start)
    if end:
        qs = qs.filter(journal__journal_date__lte=end)
    return qs


def profitability_by_dimension(companies, dimension, start=None, end=None):
    """Revenue - COGS - Expense per nilai dimensi.

    Dimension harus salah satu kunci DIMENSIONS. Line tanpa dimensi itu
    diabaikan - tidak bisa diatribusikan ke customer/project tertentu.
    """
    if dimension not in DIMENSIONS:
        raise ValueError(f'Dimensi tidak dikenal: {dimension}. Pilihan: {list(DIMENSIONS)}')
    field = DIMENSIONS[dimension][0]

    buckets = defaultdict(lambda: {
        'revenue': Decimal('0'), 'cogs': Decimal('0'), 'expense': Decimal('0'),
    })
    labels = {}

    qs = _lines(companies, start, end).exclude(**{f'{field}__isnull': True})
    for line in qs.select_related(field, 'account'):
        obj = getattr(line, field)
        key = obj.pk
        labels[key] = str(obj)
        bucket = buckets[key]
        category = line.account.account_category
        net = line.debit - line.credit
        if category == AccountCategory.REVENUE:
            bucket['revenue'] += -net      # credit-normal -> positif
        elif category == AccountCategory.COGS:
            bucket['cogs'] += net
        elif category == AccountCategory.EXPENSE:
            bucket['expense'] += net

    rows = []
    for key, b in buckets.items():
        profit = b['revenue'] - b['cogs'] - b['expense']
        rows.append({
            'key': key,
            'label': labels.get(key, str(key)),
            'revenue': b['revenue'],
            'cogs': b['cogs'],
            'expense': b['expense'],
            'gross_profit': b['revenue'] - b['cogs'],
            'profit': profit,
            'margin': (profit / b['revenue'] * 100) if b['revenue'] else Decimal('0'),
        })
    rows.sort(key=lambda r: r['profit'], reverse=True)
    return rows


def product_profitability(companies, start=None, end=None):
    """Profitability per produk dari invoice line + COGS per produk.

    Revenue dari CustomerInvoiceLine yang invoice-nya POSTED. COGS memakai
    akun cogs_account produk, dihitung dari journal line posted. COGS tidak
    bisa diatribusikan ke produk lewat JournalLine (tidak ada field product),
    jadi ini gross margin per produk, bukan net setelah alokasi.
    """
    from ar.models import CustomerInvoice, InvoiceType
    from core.models import Product

    products = Product.objects.filter(company__in=companies)
    rows = []

    revenue = defaultdict(lambda: Decimal('0'))
    if start:
        inv_qs = CustomerInvoice.objects.filter(
            company__in=companies, invoice_date__gte=start,
            invoice_type=InvoiceType.INVOICE,
            status__in=['POSTED', 'PARTIALLY_PAID', 'PAID', 'OVERDUE'])
    else:
        inv_qs = CustomerInvoice.objects.filter(
            company__in=companies, invoice_type=InvoiceType.INVOICE,
            status__in=['POSTED', 'PARTIALLY_PAID', 'PAID', 'OVERDUE'])
    if end:
        inv_qs = inv_qs.filter(invoice_date__lte=end)

    for inv in inv_qs:
        for line in inv.lines.filter(product__isnull=False):
            revenue[(line.product.company_id, line.product_id)] += line.subtotal

    cogs = defaultdict(lambda: Decimal('0'))
    for product in products:
        if not product.cogs_account_id:
            continue
        qs = JournalLine.objects.filter(
            journal__company=product.company, journal__status__in=POSTED,
            account=product.cogs_account,
        )
        if start:
            qs = qs.filter(journal__journal_date__gte=start)
        if end:
            qs = qs.filter(journal__journal_date__lte=end)
        cogs[(product.company_id, product.id)] = abs(qs.aggregate(s=Sum('debit'))['s'] or Decimal('0'))

    for product in products:
        key = (product.company_id, product.id)
        rev = revenue.get(key, Decimal('0'))
        cost = cogs.get(key, Decimal('0'))
        profit = rev - cost
        rows.append({
            'product': product,
            'company': product.company,
            'revenue': rev,
            'cogs': cost,
            'profit': profit,
            'margin': (profit / rev * 100) if rev else Decimal('0'),
        })
    rows.sort(key=lambda r: r['profit'], reverse=True)
    return rows


def customer_statement(customer, start=None, end=None):
    """PRD 13.2 - Customer statement: invoice + payment, running balance."""
    from ar.models import CustomerInvoice, CustomerPayment

    invoices = CustomerInvoice.objects.filter(customer=customer)
    payments = CustomerPayment.objects.filter(customer=customer)
    if start:
        invoices = invoices.filter(invoice_date__gte=start)
        payments = payments.filter(payment_date__gte=start)
    if end:
        invoices = invoices.filter(invoice_date__lte=end)
        payments = payments.filter(payment_date__lte=end)

    events = []
    for inv in invoices:
        if inv.status == 'DRAFT':
            continue
        events.append({
            'date': inv.invoice_date, 'type': 'INVOICE',
            'reference': inv.invoice_number, 'description': inv.invoice_type,
            'debit': (inv.total * inv.exchange_rate).quantize(Decimal('0.01')),
            'credit': Decimal('0'),
        })
    for pay in payments:
        if pay.status != 'POSTED':
            continue
        events.append({
            'date': pay.payment_date, 'type': 'PAYMENT',
            'reference': pay.payment_number, 'description': pay.payment_method or 'Payment',
            'debit': Decimal('0'),
            'credit': (pay.amount * pay.exchange_rate).quantize(Decimal('0.01')),
        })

    events.sort(key=lambda e: (e['date'], e['type'], e['reference']))
    running = Decimal('0')
    for e in events:
        running += e['debit'] - e['credit']
        e['balance'] = running
    return events, running


def vendor_statement(supplier, start=None, end=None):
    """PRD 14 - Vendor statement: bill + payment, running balance."""
    from ap.models import BillType, SupplierBill, SupplierPayment

    bills = SupplierBill.objects.filter(supplier=supplier, bill_type=BillType.BILL)
    payments = SupplierPayment.objects.filter(supplier=supplier)
    if start:
        bills = bills.filter(bill_date__gte=start)
        payments = payments.filter(payment_date__gte=start)
    if end:
        bills = bills.filter(bill_date__lte=end)
        payments = payments.filter(payment_date__lte=end)

    events = []
    for bill in bills:
        if bill.status == 'DRAFT':
            continue
        events.append({
            'date': bill.bill_date, 'type': 'BILL',
            'reference': bill.bill_number, 'description': bill.supplier_reference or 'Vendor Bill',
            'credit': (bill.total * bill.exchange_rate).quantize(Decimal('0.01')),
            'debit': Decimal('0'),
        })
    for pay in payments:
        if pay.status != 'POSTED':
            continue
        events.append({
            'date': pay.payment_date, 'type': 'PAYMENT',
            'reference': pay.payment_number, 'description': pay.payment_method or 'Payment',
            'credit': Decimal('0'),
            'debit': (pay.amount * pay.exchange_rate).quantize(Decimal('0.01')),
        })

    events.sort(key=lambda e: (e['date'], e['type'], e['reference']))
    running = Decimal('0')
    for e in events:
        running += e['credit'] - e['debit']
        e['balance'] = running
    return events, running