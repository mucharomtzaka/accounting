"""PRD 27 - Financial Reports. Semua data diambil dari General Ledger."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import F, Sum

from gl.models import Account, AccountCategory, JournalLine, JournalStatus

POSTED = [JournalStatus.POSTED, JournalStatus.RECONCILED]


def _lines(companies, start=None, end=None, branch=None, department=None, cost_center=None, project=None):
    qs = JournalLine.objects.filter(journal__company__in=companies, journal__status__in=POSTED)
    if start:
        qs = qs.filter(journal__journal_date__gte=start)
    if end:
        qs = qs.filter(journal__journal_date__lte=end)
    for field, value in (('branch', branch), ('department', department),
                         ('cost_center', cost_center), ('project', project)):
        if value:
            qs = qs.filter(**{f'journal__{field}': value})
    return qs


def trial_balance(companies, start=None, end=None, **dims):
    """Saldo per akun: opening + periode ini."""
    opening_date = None
    opening = defaultdict(lambda: Decimal('0'))

    if start:
        opening_rows = _lines(companies, end=start, **dims).exclude(
            journal__journal_date=start
        ).values('account_id').annotate(d=Sum('debit'), c=Sum('credit'))
        for row in opening_rows:
            opening[row['account_id']] = (row['d'] or Decimal('0')) - (row['c'] or Decimal('0'))
        opening_date = start

    period_rows = (
        _lines(companies, start=start, end=end, **dims)
        .values('account_id').annotate(d=Sum('debit'), c=Sum('credit'))
    )
    period = {row['account_id']: row for row in period_rows}

    account_ids = set(opening) | set(period)
    accounts = Account.objects.filter(pk__in=account_ids, company__in=companies)

    rows = []
    total_debit = total_credit = Decimal('0')
    for account in accounts.order_by('company__company_code', 'account_code'):
        d = period.get(account.id, {}).get('d') or Decimal('0')
        c = period.get(account.id, {}).get('c') or Decimal('0')
        open_balance = opening.get(account.id, Decimal('0'))
        # d - c: positif = saldo debit, negatif = saldo credit.
        # Tidak perlu heuristics normal_balance - tanda sudah membawa arah.
        balance = open_balance + d - c
        debit_balance = balance if balance > 0 else Decimal('0')
        credit_balance = -balance if balance < 0 else Decimal('0')
        total_debit += debit_balance
        total_credit += credit_balance
        rows.append({
            'company': account.company,
            'account': account,
            'opening': open_balance,
            'debit': d,
            'credit': c,
            'debit_balance': debit_balance,
            'credit_balance': credit_balance,
            'amount': debit_balance - credit_balance,
        })
    return rows, total_debit, total_credit


def balance_comparison(companies, date_from, date_to, **dims):
    """Neraca saldo perbandingan dua tanggal (mis. akhir tahun lalu vs tahun ini).

    Memakai `trial_balance` tanpa `start`: saldo tiap akun kumulatif s/d tanggal
    tersebut. Akun yang hanya muncul di salah satu sisi tetap ditampilkan
    (sisi lainnya dianggap 0), sehingga akun baru/tertutup kelihatan.
    """
    rows_from, _, _ = trial_balance(companies, end=date_from, **dims)
    rows_to, _, _ = trial_balance(companies, end=date_to, **dims)

    merged = {}
    for row in rows_from:
        merged[row['account'].pk] = {'row': row, 'from': row['amount'], 'to': Decimal('0')}
    for row in rows_to:
        account_id = row['account'].pk
        entry = merged.setdefault(account_id, {'row': row, 'from': Decimal('0'), 'to': Decimal('0')})
        entry['row'] = row
        entry['to'] = row['amount']

    rows = []
    for entry in merged.values():
        entry['delta'] = entry['to'] - entry['from']
        rows.append(entry)
    rows.sort(key=lambda e: (e['row']['company'].company_code, e['row']['account'].account_code))

    totals = {
        'from': sum((r['from'] for r in rows), Decimal('0')),
        'to': sum((r['to'] for r in rows), Decimal('0')),
        'delta': sum((r['delta'] for r in rows), Decimal('0')),
    }
    return rows, totals


def profit_and_loss(companies, start, end, **dims):
    rows, _, _ = trial_balance(companies, start=start, end=end, **dims)
    # Revenue bersifat credit-normal, jadi dibalik tandanya agar positif.
    revenue = sum((-r['amount'] for r in rows
                   if r['account'].account_category == AccountCategory.REVENUE))
    cogs = sum(r['amount'] for r in rows
               if r['account'].account_category == AccountCategory.COGS)
    expense = sum(r['amount'] for r in rows
                  if r['account'].account_category == AccountCategory.EXPENSE)
    gross_profit = revenue - cogs
    net_profit = gross_profit - expense
    return {
        'revenue': revenue, 'cogs': cogs, 'gross_profit': gross_profit,
        'expense': expense, 'net_profit': net_profit,
        'revenue_lines': [r for r in rows if r['account'].account_category == AccountCategory.REVENUE],
        'cogs_lines': [r for r in rows if r['account'].account_category == AccountCategory.COGS],
        'expense_lines': [r for r in rows if r['account'].account_category == AccountCategory.EXPENSE],
    }


def monthly_trend(companies, end, months=12, **dims):
    """Revenue / expense / net per bulan - sumber chart dashboard."""
    start = end.replace(day=1)
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)

    monthly = defaultdict(lambda: {'revenue': Decimal('0'), 'cogs': Decimal('0'),
                                   'expense': Decimal('0')})
    for line in _lines(companies, start=start, end=end, **dims).select_related('account'):
        key = (line.journal.journal_date.year, line.journal.journal_date.month)
        category = line.account.account_category
        amount = line.debit - line.credit
        if category == AccountCategory.REVENUE:
            monthly[key]['revenue'] -= amount
        elif category == AccountCategory.COGS:
            monthly[key]['cogs'] += amount
        elif category == AccountCategory.EXPENSE:
            monthly[key]['expense'] += amount

    out = []
    year, month = start.year, start.month
    for i in range(months):
        key = (year, month)
        data = monthly.get(key, {'revenue': Decimal('0'), 'cogs': Decimal('0'),
                                 'expense': Decimal('0')})
        revenue, cogs, expense = data['revenue'], data['cogs'], data['expense']
        out.append({
            'label': date(year, month, 1).strftime('%b'),
            'revenue': revenue,
            'cogs': cogs,
            'expense': expense,
            'gross_profit': revenue - cogs,
            'net_profit': revenue - cogs - expense,
        })
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out


def balance_sheet(companies, as_of, **dims):
    rows, _, _ = trial_balance(companies, end=as_of, **dims)
    assets = [r for r in rows if r['account'].account_category == AccountCategory.ASSET]
    liabilities = [r for r in rows if r['account'].account_category == AccountCategory.LIABILITY]
    equity = [r for r in rows if r['account'].account_category == AccountCategory.EQUITY]
    pnl = profit_and_loss(companies, None, as_of, **dims)

    total_assets = sum(r['amount'] for r in assets)
    total_liabilities = sum(-r['amount'] for r in liabilities)
    total_equity = sum(-r['amount'] for r in equity)
    return {
        'assets': assets, 'liabilities': liabilities, 'equity': equity,
        'total_assets': total_assets,
        'total_liabilities': total_liabilities,
        'total_equity': total_equity + pnl['net_profit'],
        'retained_profit': pnl['net_profit'],
        'is_balanced': abs(
            total_assets - (total_liabilities + total_equity + pnl['net_profit'])
        ) < Decimal('0.01'),
    }


def general_ledger(companies, account, start=None, end=None, **dims):
    """PRD 27 - GL detail dengan running balance."""
    opening = Decimal('0')
    if start:
        opening_row = (
            _lines(companies, end=start, **dims)
            .filter(account=account)
            .exclude(journal__journal_date=start)  # bukan double count dengan baris periode
            .aggregate(d=Sum('debit'), c=Sum('credit'))
        )
        opening = (opening_row['d'] or Decimal('0')) - (opening_row['c'] or Decimal('0'))

    lines = (
        _lines(companies, start=start, end=end, **dims)
        .filter(account=account)
        .select_related('journal', 'journal__company')
        .order_by('journal__journal_date', 'journal_id', 'line_no')
    )
    rows, running = [], opening
    for line in lines:
        running += line.debit - line.credit
        rows.append({'line': line, 'running_balance': running})
    return rows, opening


CASH_ACCOUNT_TYPES = ('CASH', 'BANK', 'CASH_BANK', 'PETTY_CASH')
# Akun aset jangka panjang: perubahannya investing, bukan operating.
INVESTING_ACCOUNT_TYPES = ('FIXED_ASSET', 'CONTRA_ASSET', 'OTHER_ASSET', 'FX_ADJUST')
CASH_FLOW_SECTIONS = (
    ('Operating', 'Operating'),
    ('Investing', 'Investing'),
    ('Financing', 'Financing'),
    ('Transfer', 'Transfer antar kas'),
)


def _cash_flow_section(counter_lines):
    """Section arus kas dari akun LAWAN (akun kas sendiri tidak tahu jenisnya)."""
    sections = set()
    for line in counter_lines:
        account = line.account
        if account.account_category == AccountCategory.EQUITY:
            sections.add('Financing')
        elif account.account_type in INVESTING_ACCOUNT_TYPES:
            sections.add('Investing')
        else:
            sections.add('Operating')
    if not sections:
        return 'Transfer'
    if len(sections) == 1:
        return sections.pop()
    return 'Operating'


def _cash_balance_before(companies, start, **dims):
    """Saldo kas+bank sebelum `start` (kas awal periode). Tanpa start = 0."""
    if not start:
        return Decimal('0')
    agg = (
        _lines(companies, **dims)
        .filter(journal__journal_date__lt=start, account__account_type__in=CASH_ACCOUNT_TYPES)
        .aggregate(d=Sum('debit'), c=Sum('credit'))
    )
    return (agg['d'] or Decimal('0')) - (agg['c'] or Decimal('0'))


def cash_flow(companies, start, end, **dims):
    """PRD 27 - cash flow per section + rincian baris kas + rekonsiliasi saldo.

    Perubahan kas diambil dari baris akun kas/bank; section ditentukan dari akun
    lawannya, karena akun kas tidak menyimpan jenis transaksinya. Jurnal yang
    hanya memindahkan kas antar rekening (mis. tarik tunai dari bank) masuk
    Transfer: ditampilkan gross (masuk/keluar) tapi tidak menambah net cash,
    karena total kas tidak berubah - hanya pindah rekening.
    """
    grouped = defaultdict(list)
    for line in (_lines(companies, start=start, end=end, **dims)
                 .select_related('account', 'journal', 'journal__company')):
        grouped[line.journal_id].append(line)

    sections = {
        key: {'key': key, 'label': label, 'inflow': Decimal('0'),
              'outflow': Decimal('0'), 'lines': []}
        for key, label in CASH_FLOW_SECTIONS
    }
    for journal_lines in grouped.values():
        cash_lines = [l for l in journal_lines if l.account.account_type in CASH_ACCOUNT_TYPES]
        if not cash_lines:
            continue
        counter = [l for l in journal_lines if l.account.account_type not in CASH_ACCOUNT_TYPES]
        bucket = sections[_cash_flow_section(counter)]
        for line in cash_lines:
            bucket['inflow'] += line.debit
            bucket['outflow'] += line.credit
            bucket['lines'].append({
                'line': line,
                'journal': line.journal,
                'inflow': line.debit,
                'outflow': line.credit,
                'net': line.debit - line.credit,
            })

    ordered = []
    for key, _label in CASH_FLOW_SECTIONS:
        section = sections[key]
        section['lines'].sort(key=lambda r: (r['journal'].journal_date, r['journal'].journal_number))
        section['net'] = section['inflow'] - section['outflow']
        ordered.append(section)

    # Transfer tidak ikut net cash (pindah rekening, bukan arus dari luar).
    net_cash = sum(s['net'] for s in ordered if s['key'] != 'Transfer')
    opening_cash = _cash_balance_before(companies, start, **dims)
    transfer = sections['Transfer']
    return {
        'sections': ordered,
        'has_activity': any(s['lines'] for s in ordered),
        'inflow': {s['label']: s['inflow'] for s in ordered if s['inflow']},
        'outflow': {s['label']: s['outflow'] for s in ordered if s['outflow']},
        'net_cash': net_cash,
        'opening_cash': opening_cash,
        'closing_cash': opening_cash + net_cash,
        'transfer': {'inflow': transfer['inflow'], 'outflow': transfer['outflow']},
    }


def ar_aging(company, as_of):
    """PRD 13.3 - Current / 1-30 / 31-60 / 61-90 / >90."""
    from ar.models import CustomerInvoice, InvoiceType

    buckets = {'Current': Decimal('0'), '1-30': Decimal('0'), '31-60': Decimal('0'),
               '61-90': Decimal('0'), '>90': Decimal('0')}
    invoices = CustomerInvoice.objects.filter(
        company=company, status__in=['POSTED', 'PARTIALLY_PAID', 'OVERDUE'],
        invoice_type=InvoiceType.INVOICE, invoice_date__lte=as_of,
    ).exclude(amount_paid__gte=F('total'))
    for inv in invoices:
        outstanding = (inv.total - inv.amount_paid) * inv.exchange_rate
        days = (as_of - inv.due_date).days
        if days <= 0:
            buckets['Current'] += outstanding
        elif days <= 30:
            buckets['1-30'] += outstanding
        elif days <= 60:
            buckets['31-60'] += outstanding
        elif days <= 90:
            buckets['61-90'] += outstanding
        else:
            buckets['>90'] += outstanding
    return buckets


def ap_aging(company, as_of):
    from ap.models import BillType, SupplierBill

    buckets = {'Current': Decimal('0'), '1-30': Decimal('0'), '31-60': Decimal('0'),
               '61-90': Decimal('0'), '>90': Decimal('0')}
    bills = SupplierBill.objects.filter(
        company=company, status__in=['POSTED', 'PARTIALLY_PAID', 'OVERDUE'],
        bill_type=BillType.BILL, bill_date__lte=as_of,
    ).exclude(amount_paid__gte=F('total'))
    for bill in bills:
        outstanding = (bill.total - bill.amount_paid) * bill.exchange_rate
        days = (as_of - bill.due_date).days
        if days <= 0:
            buckets['Current'] += outstanding
        elif days <= 30:
            buckets['1-30'] += outstanding
        elif days <= 60:
            buckets['31-60'] += outstanding
        elif days <= 90:
            buckets['61-90'] += outstanding
        else:
            buckets['>90'] += outstanding
    return buckets
