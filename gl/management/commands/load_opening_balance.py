"""Posting jurnal saldo awal dari CSV - jalur migrasi dari sistem lama.

Report menghitung saldo neraca kumulatif dari GL, jadi company yang baru masuk
cukup diberi satu jurnal saldo awal; tidak perlu mengimpor seluruh riwayat
transaksi. Header kolom boleh Indonesia atau Inggris, urutan bebas:

    account_code / kode_akun, debit, credit / kredit

Kolom `nama_akun`/`account_name` boleh disertakan (dipakai template unduhan
supaya mudah dibaca) - isinya diabaikan saat parsing.

Angka boleh format Indonesia (`1.234.567,89`) maupun polos (`1234567.89`).
Total debit harus sama dengan total credit. Idempotent: reference default
`OB/<tanggal>`, dan command menolak kalau sudah ada (pakai --reference lain
untuk entri saldo awal di tanggal yang sama).
"""
import csv
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_date

from core.models import Company
from gl.engine import get_period
from gl.errors import AccountingError
from gl.models import Account, JournalEntry, JournalSource
from gl.services import post_opening_balance

HEADER_ALIASES = {
    'account_code': {'account_code', 'accountcode', 'code', 'kode_akun', 'kode'},
    'debit': {'debit', 'debet'},
    'credit': {'credit', 'kredit', 'kredit_'},
}


def parse_amount(raw):
    """Angka ala Indonesia (`1.234.567,89`) atau polos (`1234567.89`)."""
    text = (raw or '').strip().replace(' ', '')
    if text in ('', '-', '0'):
        return Decimal('0')
    if ',' in text and '.' in text:
        text = text.replace('.', '').replace(',', '.')
    elif ',' in text:
        text = text.replace(',', '.')
    elif text.count('.') > 1:
        text = text.replace('.', '')
    try:
        return Decimal(text).quantize(Decimal('0.01'))
    except InvalidOperation:
        raise CommandError(f'Angka tidak valid: {raw!r}')


def map_header(cells):
    mapping = {}
    for index, cell in enumerate(cells):
        key = (cell or '').strip().lower().replace(' ', '_')
        for canonical, aliases in HEADER_ALIASES.items():
            if key in aliases:
                mapping[canonical] = index
    missing = {'account_code'} - set(mapping)
    if missing:
        raise CommandError(
            f'Kolom wajib tidak ada: {", ".join(sorted(missing))}. '
            f'Header ditemukan: {", ".join(cells)}')
    return mapping


class Command(BaseCommand):
    help = 'Posting jurnal saldo awal (migrasi dari sistem lama) dari file CSV.'

    def add_arguments(self, parser):
        parser.add_argument('--company', required=True, help='company_code tujuan')
        parser.add_argument('--date', required=True, help='tanggal saldo awal, YYYY-MM-DD')
        parser.add_argument('--file', required=True, help='path file CSV')
        parser.add_argument('--reference', default=None,
                            help='default OB/<tanggal>; dipakai untuk idempotensi')
        parser.add_argument('--dry-run', action='store_true',
                            help='hitung & validasi saja, tidak menulis jurnal')

    def handle(self, *args, **options):
        company = Company.objects.filter(company_code=options['company']).first()
        if company is None:
            available = ', '.join(Company.objects.values_list('company_code', flat=True))
            raise CommandError(
                f'Company {options["company"]!r} tidak ada. Tersedia: {available}')

        as_of = parse_date(options['date'])
        if as_of is None:
            raise CommandError(f'Tanggal {options["date"]!r} tidak valid (pakai YYYY-MM-DD).')

        reference = options['reference'] or f'OB/{as_of}'
        if JournalEntry.objects.filter(
                company=company, reference=reference, source=JournalSource.OPENING_BALANCE).exists():
            raise CommandError(
                f'Saldo awal reference {reference!r} sudah ada untuk {company.company_code}. '
                'Pakai --reference lain kalau memang entri baru.')

        rows, errors = self._read_rows(company, options['file'])
        for err in errors:
            self.stderr.write(err)
        if errors:
            raise CommandError(f'{len(errors)} baris bermasalah, tidak ada yang ditulis.')

        total_debit = sum((d for _, d, _ in rows), Decimal('0'))
        total_credit = sum((c for _, _, c in rows), Decimal('0'))
        self.stdout.write(
            f'{company.company_code} per {as_of}: {len(rows)} akun, '
            f'debit {total_debit}, credit {total_credit}, reference {reference}')

        # Validasi yang bisa dilakukan lebih awal, supaya dry-run benar-benar
        # memberi tahu masalah sebelum user menjalankan tanpa --dry-run.
        if total_debit != total_credit:
            raise CommandError(
                f'Saldo awal tidak balance: debit {total_debit} != credit {total_credit}.')
        try:
            get_period(company, as_of)
        except AccountingError as exc:
            raise CommandError(str(exc))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry-run: tidak ada jurnal yang dibuat.'))
            return

        try:
            journal = post_opening_balance(company, as_of, rows,
                                           user=None, reference=reference)
        except (ValidationError, AccountingError) as exc:
            raise CommandError(f'Gagal posting saldo awal: {exc}')
        self.stdout.write(self.style.SUCCESS(f'Jurnal saldo awal dibuat: {journal.journal_number}'))

    @staticmethod
    def _cell(cells, mapping, key):
        index = mapping.get(key)
        if index is None or index >= len(cells):
            return ''
        return cells[index]

    def _read_rows(self, company, path):
        try:
            with open(path, newline='', encoding='utf-8-sig') as handle:
                reader = csv.reader(handle)
                header = next(reader, None)
                if header is None:
                    raise CommandError('File CSV kosong.')
                mapping = map_header(header)
                rows, errors = [], []
                for number, cells in enumerate(reader, start=2):
                    if not any(cell.strip() for cell in cells):
                        continue
                    code = self._cell(cells, mapping, 'account_code').strip()
                    account = Account.objects.filter(
                        company=company, account_code=code).first()
                    if account is None:
                        errors.append(f'  baris {number}: akun {code!r} tidak ada di '
                                      f'{company.company_code}')
                        continue
                    rows.append((
                        account,
                        parse_amount(self._cell(cells, mapping, 'debit')),
                        parse_amount(self._cell(cells, mapping, 'credit')),
                    ))
        except FileNotFoundError:
            raise CommandError(f'File {path!r} tidak ditemukan.')
        return rows, errors
