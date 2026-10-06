"""PRD 16 - Bank Reconciliation matching engine."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from gl.engine import JournalBuilder, get_account
from gl.models import JournalSource

from .models import BankReconciliation, BankStatementLine, BankTransaction, BankTxStatus


def calculate_book_balance(bank_account, as_of):
    """Saldo buku = transaksi posted s/d tanggal statement."""
    total = bank_account.transactions.filter(
        status__in=[BankTxStatus.POSTED, BankTxStatus.RECONCILED],
        transaction_date__lte=as_of,
    ).aggregate(d=Sum('amount'))['d'] or Decimal('0')
    return total


def auto_match(reconciliation):
    """PRD 16 - matching engine: tanggal + nominal + deskripsi."""
    matched, unmatched = 0, 0
    candidates = (
        BankTransaction.objects.using(reconciliation._state.db)
        .filter(
            bank_account=reconciliation.bank_account,
            status=BankTxStatus.POSTED,
            reconciliation__isnull=True,
            transaction_date__lte=reconciliation.statement_date,
        )
        .order_by('transaction_date')
    )
    by_amount = {}
    for tx in candidates:
        by_amount.setdefault((tx.transaction_date, tx.amount), []).append(tx)

    for line in reconciliation.statement_lines.filter(matched_transaction__isnull=True):
        amount = abs(line.amount)
        # exact tanggal + nominal
        tx = (by_amount.get((line.statement_date, line.amount)) or [None])[0]
        if tx is None:
            # toleransi tanggal +/- 3 hari
            window = [
                (d, list(v)) for d, v in by_amount.items() if d[1] == line.amount
                and abs((line.statement_date - d[0]).days) <= 3
            ]
            if window:
                tx = window[0][1].pop(0) if len(window[0][1]) > 1 else window[0][1][0]
                if not window[0][1]:
                    del by_amount[(window[0][0], window[0][1])]
        if tx is None:
            unmatched += 1
            continue
        line.matched_transaction = tx
        line.match_type = 'AUTO'
        line.save(update_fields=['matched_transaction', 'match_type'])
        matched += 1
    return matched, unmatched


def match_line(line, transaction, match_type='MANUAL'):
    line.matched_transaction = transaction
    line.match_type = match_type
    line.save(update_fields=['matched_transaction', 'match_type'])
    return line


def complete(reconciliation, user=None):
    unmatched = reconciliation.statement_lines.filter(matched_transaction__isnull=True).count()
    if unmatched:
        raise ValueError(f'{unmatched} baris statement belum di-match.')

    reconciliation.book_balance = calculate_book_balance(
        reconciliation.bank_account, reconciliation.statement_date
    )
    reconciliation.statement_balance = reconciliation.start_balance + sum(
        (line.amount for line in reconciliation.statement_lines.all()), Decimal('0')
    )
    reconciliation.status = BankReconciliation.Status.COMPLETED
    reconciliation.completed_by = user
    reconciliation.completed_at = timezone.now()
    reconciliation.save()

    reconciliation.bank_account.transactions.filter(
        transaction_date__lte=reconciliation.statement_date,
        status=BankTxStatus.POSTED,
    ).update(reconciliation=reconciliation, status=BankTxStatus.RECONCILED)

    BankReconciliation.objects.filter(
        bank_account=reconciliation.bank_account, status=BankReconciliation.Status.COMPLETED
    ).exclude(pk=reconciliation.pk).update(status=BankReconciliation.Status.IN_PROGRESS)

    return reconciliation


def import_statement(bank_account, reconciliation, rows):
    """rows: iterable of dict(date, description, reference, debit, credit, balance)."""
    created = []
    for row in rows:
        created.append(BankStatementLine.objects.create(
            bank_account=bank_account, reconciliation=reconciliation, **row
        ))
    return created


def outstanding_transactions(bank_account, as_of):
    """Transaksi di buku yang belum muncul di statement."""
    return bank_account.transactions.filter(
        status=BankTxStatus.POSTED,
        reconciliation__isnull=True,
        transaction_date__lte=as_of,
    )


def stale_unreconciled(bank_account, days=30):
    cutoff = timezone.localdate() - timedelta(days=days)
    return outstanding_transactions(bank_account, cutoff)


def parse_statement_csv(file_obj):
    """Parse CSV bank statement.

    Format yang diterima (header opsional, kolom boleh berurutan apa saja):
        tanggal, referensi, uraian, debet, kredit, saldo
    Kolom recognized (case-insensitive):
        date/transaction_date/tanggal/tgl
        reference/reference_no/referensi
        description/uraian/memo
        debit/debet/d
        credit/kredit/c
        balance/saldo/bal

    Angka boleh pakai separator ribuan dan tanda kurung untuk negatif:
        "1.500.000" -> 1500000
        "(500.000)"  -> -500000
    Baris kosong dilewati. Baris yang kolomnya tidak lengkap dikumpulkan
    sebagai `errors` sehingga admin bisa lihat mana yang gagal.
    """
    import csv
    import re
    from datetime import datetime
    from decimal import Decimal, InvalidOperation
    from io import TextIOWrapper

    if hasattr(file_obj, 'read') and not hasattr(file_obj, 'encoding'):
        file_obj = TextIOWrapper(file_obj, encoding='utf-8-sig')

    aliases = {
        'date': {'date', 'transaction_date', 'tanggal', 'tgl', 'value_date'},
        'reference': {'reference', 'reference_no', 'ref', 'referensi'},
        'description': {'description', 'uraian', 'memo', 'detail', 'narrative'},
        'debit': {'debit', 'debet', 'd', 'out', 'outflow', 'money_out'},
        'credit': {'credit', 'kredit', 'c', 'in', 'inflow', 'money_in'},
        'balance': {'balance', 'saldo', 'bal', 'running_balance'},
    }
    header_map = {}
    rows, errors = [], []

    def normalize_number(raw):
        raw = str(raw or '').strip()
        if not raw:
            return Decimal('0')
        neg = raw.startswith('(') and raw.endswith(')')
        raw = raw.strip('()')
        raw = re.sub(r'[^0-9,.\-+]', '', raw)
        # anggap pemisah terakhir sebagai desimal; sisanya ribuan
        for sep in (',', '.'):
            if raw.count(sep) == 1:
                # satu titik/koma saja: bisa desimal atau ribuan
                parts = raw.split(sep)
                if len(parts[-1]) == 3 and len(parts[0]) <= 3 and sep == '.':
                    raw = raw.replace('.', '')          # 1.500 -> 1500
                elif len(parts[-1]) == 3 and len(parts[0]) > 3 and sep == '.':
                    raw = raw.replace('.', '')          # 1.500.000 style single -> 150000
                elif sep == ',' and len(parts[-1]) == 3:
                    raw = raw.replace(',', '')          # 1,500 -> 1500
            else:
                raw = raw.replace(sep, '')
        raw = raw.replace(',', '.')
        if raw in ('', '-', '+'):
            return Decimal('0')
        try:
            value = Decimal(raw)
        except InvalidOperation:
            return Decimal('0')
        return -value if neg else value

    def parse_date(raw):
        raw = str(raw or '').strip()
        if not raw:
            return None
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%m/%d/%Y', '%Y/%m/%d'):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        return None

    reader = csv.reader(file_obj)
    header_found = False
    for line_no, raw_row in enumerate(reader, start=1):
        if not raw_row or all(not str(c).strip() for c in raw_row):
            continue

        # Baris header hanya jika >=2 cell-nya nama kolom yang dikenal.
        # Kalau tidak, CSV dianggap tanpa header (positional).
        candidate = {}
        for idx, cell in enumerate(raw_row):
            key = str(cell).strip().lower().replace(' ', '_')
            for field, options in aliases.items():
                if key in options and field not in candidate:
                    candidate[field] = idx
        if not header_found and len(candidate) >= 2:
            header_found = True
            if 'date' not in candidate:
                errors.append({'line': line_no, 'reason': 'Header tidak mengandung kolom tanggal.'})
                candidate = {}
                header_found = False
            else:
                header_map = candidate
            continue

        if header_found and not header_map:
            continue

        row = {'date': None, 'reference': '', 'description': '',
               'debit': Decimal('0'), 'credit': Decimal('0'), 'balance': None}
        idx_map = header_map if header_found else {
            'date': 0, 'reference': 1, 'description': 2, 'debit': 3, 'credit': 4, 'balance': 5,
        }

        for field, idx in idx_map.items():
            if idx >= len(raw_row):
                continue
            cell = raw_row[idx]
            if field == 'date':
                row['date'] = parse_date(cell)
            elif field == 'debit':
                row['debit'] = abs(normalize_number(cell))
            elif field == 'credit':
                row['credit'] = abs(normalize_number(cell))
            elif field == 'balance':
                row['balance'] = normalize_number(cell) if str(cell).strip() else None
            else:
                row[field] = str(cell).strip()

        if row['date'] is None:
            errors.append({'line': line_no,
                           'reason': f'Tanggal tidak dikenali: {raw_row[0] if raw_row else ""}'})
            continue
        if row['debit'] == 0 and row['credit'] == 0:
            errors.append({'line': line_no, 'reason': 'Debet dan kredit sama-sama kosong/0.'})
            continue

        rows.append({
            'statement_date': row['date'],
            'value_date': row['date'],
            'reference': row['reference'],
            'description': row['description'],
            'debit': row['debit'],
            'credit': row['credit'],
            'balance': row['balance'],
        })
    return rows, errors


def import_statement_from_csv(bank_account, reconciliation, file_obj):
    """PRD 16 - Import bank statement dari CSV file."""
    rows, errors = parse_statement_csv(file_obj)
    created = import_statement(bank_account, reconciliation, rows)
    return created, errors


def post_bank_transaction(bank_tx, user=None):
    """PRD 15 - Post cash/bank receipt, payment, transfer, charge, interest ke GL."""
    from django.core.exceptions import ValidationError

    if bank_tx.status != BankTxStatus.DRAFT:
        raise ValidationError(f'Transaksi {bank_tx.transaction_number} sudah diposting.')
    if not bank_tx.amount:
        raise ValidationError('Amount harus lebih dari 0.')

    company = bank_tx.company
    bank_gl = bank_tx.bank_account.gl_account
    builder = JournalBuilder(
        company, bank_tx.transaction_date,
        source=JournalSource.BANK,
        description=f'{bank_tx.get_tx_type_display()} {bank_tx.transaction_number}',
        currency=bank_tx.currency_id, exchange_rate=bank_tx.exchange_rate,
        branch=bank_tx.branch, reference=bank_tx.transaction_number, user=user,
    )

    if bank_tx.tx_type == 'TRANSFER':
        # transfer antar rekening bank dalam company yang sama
        counter = bank_tx.counterparty_bank_account
        if not counter or counter.gl_account_id == bank_gl.id:
            raise ValidationError('Transfer harus punya counterparty bank account yang berbeda.')
        builder.debit(counter.gl_account, bank_tx.amount, 'Transfer masuk')
        builder.credit(bank_gl, bank_tx.amount, 'Transfer keluar')
    else:
        mapping_key = {
            'RECEIPT': 'RECEIPT_INCOME',
            'PAYMENT': 'PAYMENT_EXPENSE',
            'INTEREST': 'INTEREST_INCOME',
        }.get(bank_tx.tx_type)
        account = get_account(company, 'BANK', mapping_key, required=False)

        if bank_tx.tx_type == 'RECEIPT':
            if account is None:
                account = _first_postable(company, 'REVENUE') or bank_gl
            builder.debit(bank_gl, bank_tx.amount, 'Penerimaan')
            builder.credit(account, bank_tx.amount, 'Pendapatan')
        elif bank_tx.tx_type == 'INTEREST':
            if account is None:
                account = _first_postable(company, 'REVENUE') or bank_gl
            builder.debit(bank_gl, bank_tx.amount, 'Bunga')
            builder.credit(account, bank_tx.amount, 'Pendapatan bunga')
        else:  # PAYMENT / CHARGE
            if bank_tx.tx_type == 'PAYMENT' and account is None:
                account = _first_postable(company, 'EXPENSE') or bank_gl
            if bank_tx.tx_type == 'PAYMENT':
                builder.debit(account, bank_tx.amount, 'Pengeluaran')
                builder.credit(bank_gl, bank_tx.amount, 'Pembayaran')
            else:
                fee_account = get_account(company, 'BANK', 'BANK_CHARGE', required=False) or bank_gl
                builder.debit(fee_account, bank_tx.amount, 'Biaya bank')
                builder.credit(bank_gl, bank_tx.amount, 'Biaya rekening')

    journal = builder.save(auto_post=True)
    BankTransaction.objects.filter(pk=bank_tx.pk).update(status=BankTxStatus.POSTED, journal=journal)
    bank_tx.status = BankTxStatus.POSTED
    bank_tx.journal = journal
    return journal


def _first_postable(company, category_code):
    try:
        from gl.models import Account, AccountCategory
        category_map = {
            'REVENUE': AccountCategory.REVENUE,
            'EXPENSE': AccountCategory.EXPENSE,
        }
        cat = category_map.get(category_code)
        if cat is None:
            return None
        return Account.objects.filter(
            company=company, account_category=cat, is_postable=True, is_active=True
        ).first()
    except Exception:
        return None
