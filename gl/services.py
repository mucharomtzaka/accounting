"""PRD 10 + 26 - Period / Fiscal Year Closing.

Alur yang didukung:

    Bulanan : OPEN -> CLOSING (review checklist) -> CLOSED (dikunci)
    Tahunan : setelah semua period CLOSING/CLOSED -> jurnal penutup
              (pendapatan/beban -> Laba Ditahan) -> fiscal year dikunci.

Opening balance tidak butuh jurnal khusus: saldo akun neraca terbawa karena
report menghitung kumulatif dari GL (lihat reports/engine.py trial_balance).
Yang dihasilkan jurnal penutup adalah akun P&L jadi nol di tahun berikutnya.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import F, Sum
from django.utils import timezone

from .models import (
    AccountCategory, AccountMapping, FiscalYear, JournalEntry, JournalSource,
    JournalStatus, PeriodStatus,
)


def open_period(period):
    period.status = PeriodStatus.OPEN
    period.closing_by = None
    period.closing_at = None
    period.save(update_fields=['status', 'closing_by', 'closing_at', 'updated_at'])
    return period


def period_closing_checklist(period):
    """Checklist month-end (PRD 26) + mana yang menghalangi closing.

    Hanya item yang benar-benar bisa diperiksa otomatis dari data yang diberi
    flag `blocking`; sisanya info manual (mis. penyusutan aset, accrual) supaya
    user tahu urutannya tanpa aplikasi mengklaim sudah memvalidasi.
    """
    from bank.models import BankTransaction
    from ar.models import CustomerInvoice
    from ap.models import SupplierBill

    company = period.company
    open_statuses = [
        JournalStatus.DRAFT, JournalStatus.SUBMITTED, JournalStatus.APPROVED,
    ]
    unposted_journals = JournalEntry.objects.filter(
        company=company, period=period, status__in=open_statuses).count()
    unbalanced = (
        JournalEntry.objects.filter(company=company, period=period, status=JournalStatus.POSTED)
        .annotate(d=Sum('lines__debit'), c=Sum('lines__credit'))
        .filter(d__isnull=False)
        .exclude(d=F('c'))
        .count()
    )
    draft_invoices = CustomerInvoice.objects.filter(
        company=company, status='DRAFT',
        invoice_date__range=(period.start_date, period.end_date)).count()
    draft_bills = SupplierBill.objects.filter(
        company=company, status='DRAFT',
        bill_date__range=(period.start_date, period.end_date)).count()
    unposted_bank = BankTransaction.objects.filter(
        company=company, status='DRAFT',
        transaction_date__range=(period.start_date, period.end_date)).count()

    return [
        {'key': 'journals_posted', 'label': 'Semua jurnal sudah posted',
         'done': unposted_journals == 0, 'blocking': True,
         'detail': f'{unposted_journals} jurnal belum posted'},
        {'key': 'journals_balanced', 'label': 'Jurnal posted balance',
         'done': unbalanced == 0, 'blocking': True,
         'detail': f'{unbalanced} jurnal tidak balance'},
        {'key': 'ar_draft', 'label': 'Invoice AR draft selesai',
         'done': draft_invoices == 0, 'blocking': True,
         'detail': f'{draft_invoices} invoice draft'},
        {'key': 'ap_draft', 'label': 'Tagihan AP draft selesai',
         'done': draft_bills == 0, 'blocking': True,
         'detail': f'{draft_bills} tagihan draft'},
        {'key': 'bank_posted', 'label': 'Transaksi bank sudah posted',
         'done': unposted_bank == 0, 'blocking': True,
         'detail': f'{unposted_bank} transaksi bank belum posted'},
        {'key': 'depreciation', 'label': 'Penyusutan aset tetap dijalankan',
         'done': True, 'blocking': False,
         'detail': 'cek manual: FixedAsset -> action Depreciate'},
        {'key': 'fx_revaluation', 'label': 'FX revaluation (kalau ada valas)',
         'done': True, 'blocking': False,
         'detail': 'AccountingPeriod -> action FX revaluation'},
        {'key': 'reconciliation', 'label': 'AR/AP/bank reconciliation ditinjau',
         'done': True, 'blocking': False,
         'detail': 'cek manual sebelum approve closing'},
    ]


def closing_blockers(period):
    return [item for item in period_closing_checklist(period)
            if item['blocking'] and not item['done']]


def start_closing(period, user=None):
    """OPEN -> CLOSING: mulai review. Ditolak kalau checklist blocking gagal."""
    if period.status == PeriodStatus.CLOSED:
        raise ValidationError('Period sudah CLOSED.')
    if period.fiscal_year.is_closed:
        raise ValidationError(f'Fiscal year {period.fiscal_year.name} sudah ditutup.')
    blockers = closing_blockers(period)
    if blockers:
        detail = '; '.join(f"{b['label']} ({b['detail']})" for b in blockers)
        raise ValidationError(f'Closing belum bisa dimulai: {detail}')
    period.status = PeriodStatus.CLOSING
    period.closing_by = user
    period.closing_at = timezone.now()
    period.save(update_fields=['status', 'closing_by', 'closing_at', 'updated_at'])
    return period


def close_period(period, user=None, notes=''):
    """CLOSING -> CLOSED: kunci final. OPEN langsung juga diizinkan (jalur lama)."""
    if period.status == PeriodStatus.CLOSED:
        return period

    broken = (
        JournalEntry.objects.filter(period=period, status=JournalStatus.POSTED)
        .annotate(d=Sum('lines__debit'), c=Sum('lines__credit'))
        .filter(d__isnull=False)
        .exclude(d=F('c'))
        .exists()
    )
    if broken:
        raise ValidationError('Ada journal posted yang tidak balance. Perbaiki sebelum menutup period.')

    period.status = PeriodStatus.CLOSED
    period.closed_by = user
    period.closed_at = timezone.now()
    period.notes = notes or period.notes
    period.save()
    return period


def reopen_period(period, user=None, notes=''):
    if period.fiscal_year.is_closed:
        raise ValidationError(
            f'Fiscal year {period.fiscal_year.name} sudah ditutup. '
            'Tidak bisa reopen period tanpa membatalkan tutup buku tahunan.'
        )
    period.status = PeriodStatus.OPEN
    period.closing_by = None
    period.closing_at = None
    period.closed_by = None
    period.closed_at = None
    period.notes = notes or 'Reopened'
    period.save()
    return period


def _retained_earnings_account(company):
    """Akun Laba Ditahan: mapping dulu, lalu tipe akun, terakhir kode 3201."""
    mapping = AccountMapping.objects.filter(
        company=company, module='EQUITY', key='RETAINED_EARNINGS', is_active=True
    ).select_related('account').first()
    if mapping:
        return mapping.account
    from .models import Account

    account = Account.objects.filter(company=company, account_type='RETAINED_EARNINGS').first()
    if account:
        return account
    return Account.objects.filter(company=company, account_code='3201').first()


def close_fiscal_year(fiscal_year, user=None):
    """Tutup buku tahunan: jurnal penutup P&L -> Laba Ditahan, lalu kunci.

    Semua period harus sudah CLOSING (review bulanan selesai) atau CLOSED.
    Period terakhir dibuka sementara untuk memposting jurnal penutup - tanpa ini
    jurnal penutup tidak bisa masuk period yang sudah dikunci (Rule 3).
    Idempotent: kalau sudah is_closed, langsung return.
    """
    from .engine import JournalBuilder

    if fiscal_year.is_closed:
        return fiscal_year

    company = fiscal_year.company
    periods = list(fiscal_year.periods.order_by('start_date'))
    if not periods:
        raise ValidationError(f'Fiscal year {fiscal_year.name} belum punya accounting period.')

    open_periods = [p for p in periods if p.status == PeriodStatus.OPEN]
    if open_periods:
        codes = ', '.join(p.period_code for p in open_periods)
        raise ValidationError(f'Period masih OPEN: {codes}. Selesaikan closing bulanan dulu.')

    retained = _retained_earnings_account(company)
    if retained is None:
        raise ValidationError('Akun Laba Ditahan (RETAINED_EARNINGS / 3201) tidak ditemukan.')

    # Sisa saldo akun P&L sepanjang fiscal year. Positif = saldo debit (beban).
    balances = (
        JournalEntry.objects.filter(
            company=company, status__in=[JournalStatus.POSTED, JournalStatus.RECONCILED],
            journal_date__range=(fiscal_year.start_date, fiscal_year.end_date),
        )
        .values('lines__account')
        .annotate(debit=Sum('lines__debit'), credit=Sum('lines__credit'))
    )
    from .models import Account

    pnl_accounts = Account.objects.filter(
        company=company,
        account_category__in=[AccountCategory.REVENUE, AccountCategory.COGS, AccountCategory.EXPENSE],
        pk__in=[b['lines__account'] for b in balances],
    )
    lines = []
    for account in pnl_accounts:
        row = next(b for b in balances if b['lines__account'] == account.pk)
        debit = row['debit'] or Decimal('0')
        credit = row['credit'] or Decimal('0')
        balance = debit - credit
        if balance == 0:
            continue
        lines.append((account, balance))

    last_period = periods[-1]
    was_closed = last_period.status == PeriodStatus.CLOSED
    if was_closed:
        # buka sementara untuk jurnal penutup; fiscal year belum is_closed jadi
        # ini bukan pelanggaran lock, hanya mekanisme internal.
        last_period.status = PeriodStatus.OPEN
        last_period.save(update_fields=['status', 'updated_at'])

    if lines:
        builder = JournalBuilder(
            company, fiscal_year.end_date, source=JournalSource.CLOSING,
            description=f'Jurnal penutup fiscal year {fiscal_year.name}',
            reference=f'YE/{fiscal_year.name}', user=user)
        net = Decimal('0')
        for account, balance in lines:
            if balance > 0:  # saldo debit (beban/HPP) -> nol-kan dengan credit
                builder.credit(account, balance, f'Tutup {account.account_code}')
                net -= balance
            else:
                builder.debit(account, -balance, f'Tutup {account.account_code}')
                net += -balance
        if net > 0:
            builder.credit(retained, net, 'Laba tahun berjalan ke Laba Ditahan')
        elif net < 0:
            builder.debit(retained, -net, 'Rugi tahun berjalan ke Laba Ditahan')
        builder.save(auto_post=True)

    # Kunci ulang semua period + fiscal year.
    now = timezone.now()
    for period in periods:
        if period.status != PeriodStatus.CLOSED:
            period.status = PeriodStatus.CLOSED
            period.closed_by = user
            period.closed_at = now
            period.save(update_fields=['status', 'closed_by', 'closed_at', 'updated_at'])

    fiscal_year.is_closed = True
    fiscal_year.closed_by = user
    fiscal_year.closed_at = now
    fiscal_year.save(update_fields=['is_closed', 'closed_by', 'closed_at', 'updated_at'])
    return fiscal_year


def reopen_fiscal_year(fiscal_year, user=None, reason=''):
    """Buka tutup buku tahunan: batalkan jurnal penutup (reversal) + lepas kunci.

    `is_closed` juga sempat bisa dicentang manual dari form admin, sehingga
    muncul state "closed" tanpa jurnal penutup maupun jejak audit. Fungsi ini
    menangani keduanya: kalau ada jurnal `YE/<tahun>` yang POSTED, di-reversal
    pada tanggal jurnalnya (period-nya dibuka sementara) supaya saldo P&L
    kembali seperti sebelum tutup buku.
    """
    if not fiscal_year.is_closed:
        return fiscal_year

    closing = (
        JournalEntry.objects.filter(
            company=fiscal_year.company, source=JournalSource.CLOSING,
            reference=f'YE/{fiscal_year.name}',
        )
        .exclude(status=JournalStatus.VOID)
        .first()
    )
    if closing is not None and closing.status in (
            JournalStatus.POSTED, JournalStatus.RECONCILED):
        period = closing.period
        if period.status == PeriodStatus.CLOSED:
            period.status = PeriodStatus.OPEN
            period.closed_by = None
            period.closed_at = None
            period.save(update_fields=[
                'status', 'closed_by', 'closed_at', 'updated_at'])
        from .engine import reverse_journal

        reverse_journal(closing, user=user,
                        reason=reason or f'Reopen fiscal year {fiscal_year.name}',
                        on_date=closing.journal_date)

    fiscal_year.is_closed = False
    fiscal_year.closed_by = None
    fiscal_year.closed_at = None
    fiscal_year.save(update_fields=['is_closed', 'closed_by', 'closed_at', 'updated_at'])
    return fiscal_year


def post_opening_balance(company, as_of, rows, user=None, reference=None):
    """Jurnal saldo awal (migrasi dari sistem lama).

    `rows` = iterable (account, debit, credit). Akun wajib milik company dan
    postable; total debit harus sama dengan total credit dan tidak nol. Tanggal
    harus jatuh di period yang masih OPEN (dijaga `JournalBuilder`).
    """
    from .engine import JournalBuilder
    from .models import Account

    builder = JournalBuilder(
        company, as_of, source=JournalSource.OPENING_BALANCE,
        description=f'Saldo awal per {as_of}',
        reference=reference or f'OB/{as_of}', user=user,
    )
    total_debit = total_credit = Decimal('0')
    for account, debit, credit in rows:
        if not isinstance(account, Account):
            raise ValidationError('Baris saldo awal harus memakai objek Account.')
        if account.company_id != company.pk:
            raise ValidationError(
                f'Akun {account.account_code} bukan milik {company.company_code}.')
        if not account.is_postable:
            raise ValidationError(f'Akun {account.account_code} tidak postable.')
        debit = Decimal(debit or 0)
        credit = Decimal(credit or 0)
        if debit < 0 or credit < 0:
            raise ValidationError(
                f'Akun {account.account_code}: nilai tidak boleh negatif '
                '(tukar posisi debit/credit).')
        if debit and credit:
            raise ValidationError(
                f'Akun {account.account_code}: isi debit atau credit, bukan keduanya.')
        if debit > 0:
            builder.debit(account, debit, f'Saldo awal {account.account_code}')
            total_debit += debit
        elif credit > 0:
            builder.credit(account, credit, f'Saldo awal {account.account_code}')
            total_credit += credit
    if total_debit != total_credit:
        raise ValidationError(
            f'Saldo awal tidak balance: debit {total_debit} != credit {total_credit}.')
    if total_debit == 0:
        raise ValidationError('Saldo awal kosong.')
    return builder.save(auto_post=True)


def generate_periods(fiscal_year):
    """Buat 12 period bulanan dari tanggal fiscal year."""
    from .models import AccountingPeriod

    start = fiscal_year.start_date
    for month in range(12):
        y = start.year + (start.month - 1 + month) // 12
        m = (start.month - 1 + month) % 12 + 1
        first = date(y, m, 1)
        last = date(y + (m == 12), (m % 12) + 1, 1).toordinal() - 1
        last = date.fromordinal(last)
        AccountingPeriod.objects.get_or_create(
            company=fiscal_year.company,
            period_code=f'{y}-{m:02d}',
            defaults={
                'fiscal_year': fiscal_year,
                'period_name': first.strftime('%B %Y'),
                'start_date': first,
                'end_date': last,
            },
        )

def revalue_fx(company, as_of, currency=None):
    """PRD 23 - Currency revaluation: unrealized FX gain/loss.

    Hanya akun moneter NERACA (asset & liability) dalam valuta asing yang
    direvaluasi - akun pendapatan/biaya sudah tertutup ke P&L dan tidak ikut
    dihitung, kalau tidak foreign amount akan terhitung dua kali.

    Posisi moneter = sum(debit - credit) akun asset/liabilitycurrency tsb.
    Unrealized     = (posisi * rate_terakhir) - nilai_base_sudah_tercatat.

        rate naik -> gain : Cr FX Gain  / Dr FX Adjustment
        rate turun-> loss : Dr FX Loss  / Cr FX Adjustment

    Return list of dict per currency:
        {currency, foreign_amount, base_amount, rate, unrealized, journal|error}
    """
    from collections import defaultdict
    from decimal import Decimal

    from django.core.exceptions import ValidationError

    from core.models import ExchangeRate
    from .engine import JournalBuilder
    from .models import AccountCategory, AccountingPeriod, JournalLine

    base_currency = company.base_currency
    period = AccountingPeriod.objects.filter(
        company=company, start_date__lte=as_of, end_date__gte=as_of
    ).first()
    if period is None:
        raise ValidationError(f'Tidak ada accounting period untuk {company.company_code} per {as_of}.')
    if period.is_locked:
        raise ValidationError(f'Period {period.period_code} CLOSED.')

    posted = [JournalStatus.POSTED, JournalStatus.RECONCILED]
    qs = JournalLine.objects.filter(
        journal__company=company,
        journal__status__in=posted,
        journal__journal_date__lte=as_of,
        account__account_category__in=[AccountCategory.ASSET, AccountCategory.LIABILITY],
    ).exclude(
        journal__currency_id=base_currency,
    ).exclude(
        # jangan hitung jurnal revaluasi sebelumnya (1390 adalah akun internal)
        journal__reference__startswith='FXREV/',
    )
    if currency:
        qs = qs.filter(journal__currency_id=currency)

    foreign_amount = defaultdict(lambda: Decimal('0'))
    base_amount = defaultdict(lambda: Decimal('0'))
    for line in qs:
        code = line.journal.currency_id
        foreign_amount[code] += (line.debit or Decimal('0')) - (line.credit or Decimal('0'))
        base_amount[code] += line.amount_base

    results = []
    for curr_code, position in foreign_amount.items():
        if position == 0:
            continue

        # Kurs company sendiri didahulukan, baru global (tanpa company).
        rate_row = (
            ExchangeRate.objects.filter(
                currency_id=curr_code, company=company, rate_date__lte=as_of
            ).order_by('-rate_date').first()
            or ExchangeRate.objects.filter(
                currency_id=curr_code, company__isnull=True, rate_date__lte=as_of
            ).order_by('-rate_date').first()
        )
        if rate_row is None:
            results.append({'currency': curr_code, 'foreign_amount': position,
                            'error': f'Tidak ada ExchangeRate untuk {curr_code} per {as_of}.'})
            continue

        rate_after = rate_row.rate
        new_base = (position * rate_after).quantize(Decimal('0.01'))
        unrealized = (new_base - base_amount[curr_code]).quantize(Decimal('0.01'))
        if abs(unrealized) < Decimal('0.01'):
            continue

        fx_gain = _fx_account(company, 'FX_GAIN')
        fx_loss = _fx_account(company, 'FX_LOSS')
        fx_adjust = _fx_account(company, 'FX_ADJUST')
        if fx_gain is None or fx_loss is None or fx_adjust is None:
            results.append({
                'currency': curr_code, 'foreign_amount': position,
                'unrealized': unrealized,
                'error': 'Account mapping TAX/FX_GAIN, TAX/FX_LOSS, dan TAX/FX_ADJUST '
                         'belum dikonfigurasi lengkap.',
            })
            continue

        builder = JournalBuilder(
            company, as_of,
            description=f'FX revaluation {curr_code} per {as_of} - unrealized',
            currency=curr_code,
            reference=f'FXREV/{curr_code}/{as_of}',
        )
        if unrealized > 0:
            builder.credit(fx_gain, unrealized, f'Unrealized FX gain {curr_code}')
            builder.debit(fx_adjust, unrealized, f'FX adjustment {curr_code}')
        else:
            builder.debit(fx_loss, -unrealized, f'Unrealized FX loss {curr_code}')
            builder.credit(fx_adjust, -unrealized, f'FX adjustment {curr_code}')

        journal = builder.save(auto_post=True)
        results.append({
            'currency': curr_code,
            'foreign_amount': position,
            'base_amount': base_amount[curr_code],
            'rate': rate_after,
            'unrealized': unrealized,
            'journal': journal,
        })
    return results


def _fx_account(company, key):
    from .models import AccountMapping

    mapping = AccountMapping.objects.filter(
        company=company, module='TAX', key=key, is_active=True
    ).select_related('account').first()
    return mapping.account if mapping else None
