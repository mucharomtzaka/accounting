"""Test import/export Chart of Accounts lewat CSV.

Dua lapis:

1. `parse_account_csv` + `validate_rows` - murni teks, tanpa DB (SimpleTestCase).
   Menangkap header salah, kode bukan angka, duplikat dalam file, nilai enum
   ngawur, dan kolom boolean yang salah tulis.
2. `build_plan` + `commit_plan` - butuh DB. Menyelimati scoping company,
   resolusi induk dari file yang sama, deteksi siklus, dan rollback atomik.

Skenario siklus penting karena parent_code ditulis bebas di CSV; tanpa
deteksi, satu siklus akan membuat `level`/report rekursif meledak.
"""

from io import BytesIO

from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import Company, Currency, Group, User, UserCompanyAccess
from gl import csv_io
from gl.models import Account

HEADER = ','.join(csv_io.COLUMNS)


def csv_bytes(*rows):
    return BytesIO(('\r\n'.join([HEADER, *rows]) + '\r\n').encode('utf-8'))


class ParseCsvTests(SimpleTestCase):
    """Fase tanpa DB."""

    def test_template_header_matches_column_order(self):
        rows = csv_io.template_rows()
        self.assertEqual(rows[0], csv_io.COLUMNS)
        for row in rows[1:]:
            self.assertEqual(len(row), len(csv_io.COLUMNS), f'baris contoh: {row}')

    def test_render_csv_has_bom_and_crlf(self):
        text = csv_io.render_csv(csv_io.template_rows())
        self.assertTrue(text.startswith('\ufeff'))
        self.assertIn('\r\n', text)

    def test_alternative_headers_and_any_order(self):
        upload = BytesIO(
            'Nama Akun;Kode;Kategori\n'
            'Kas Tunai;1101;1\n'.encode('utf-8')
        )
        header_map, rows, errors = csv_io.parse_account_csv(upload)
        self.assertEqual(errors, [])
        clean, errors = csv_io.validate_rows(header_map, rows)
        self.assertEqual(errors, [])
        self.assertEqual(clean[0]['code'], '1101')
        self.assertEqual(clean[0]['name'], 'Kas Tunai')

    def test_missing_required_column_is_rejected(self):
        upload = BytesIO('kode_akun,nama_akun\n1101,Kas\n'.encode('utf-8'))
        _, _, errors = csv_io.parse_account_csv(upload)
        self.assertEqual(len(errors), 1)
        self.assertIn('account_category', errors[0]['reason'])
        self.assertIn('kategori', errors[0]['reason'])

    def test_empty_file_rejected(self):
        _, _, errors = csv_io.parse_account_csv(BytesIO(b''))
        self.assertTrue(errors)

    def test_duplicate_code_inside_file(self):
        header_map, rows, errors = csv_io.parse_account_csv(csv_bytes(
            '1101,Kas Tunai,1 - Asset',
            '1101,Kas Tunai Lagi,1 - Asset',
        ))
        self.assertEqual(errors, [])
        clean, errors = csv_io.validate_rows(header_map, rows)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]['line'], 3)
        self.assertIn('duplikat', errors[0]['reason'])
        # Baris pertama tetap valid - bukan berarti file dibuang semua.
        self.assertEqual([r['code'] for r in clean], ['1101'])

    def test_non_numeric_code_rejected(self):
        header_map, rows, _ = csv_io.parse_account_csv(csv_bytes('1A01,Kas,1 - Asset'))
        clean, errors = csv_io.validate_rows(header_map, rows)
        self.assertEqual(clean, [])
        self.assertIn('hanya angka', errors[0]['reason'])

    def test_bad_boolean_rejected(self):
        header_map, rows, _ = csv_io.parse_account_csv(csv_bytes('1101,Kas,1 - Asset,,,,,,,mungkin'))
        clean, errors = csv_io.validate_rows(header_map, rows)
        self.assertEqual(clean, [])
        self.assertIn('Y/Ya', errors[0]['reason'])

    def test_normal_balance_defaults_from_category(self):
        header_map, rows, _ = csv_io.parse_account_csv(csv_bytes('2101,Utang Usaha,2 - Liability'))
        clean, errors = csv_io.validate_rows(header_map, rows)
        self.assertEqual(errors, [])
        self.assertEqual(clean[0]['values']['normal_balance'], 'C')

    def test_account_type_falls_back_to_category_name(self):
        self.assertEqual(csv_io.category_type_name(2), 'LIABILITY')
        self.assertEqual(csv_io.category_type_name(5), 'COGS')
        self.assertEqual(csv_io.category_type_name(None), 'OTHER')

    def test_shipped_template_roundtrips_through_import(self):
        header_map, rows, errors = csv_io.parse_account_csv(
            BytesIO(csv_io.render_csv(csv_io.template_rows()).encode('utf-8')))
        self.assertEqual(errors, [])
        clean, errors = csv_io.validate_rows(header_map, rows)
        self.assertEqual(errors, [])
        self.assertEqual(len(clean), len(csv_io.TEMPLATE_ROWS))
        self.assertEqual(clean[1]['parent_code'], '1000')
        self.assertEqual(clean[1]['values']['is_postable'], False)

    def test_blank_cells_are_not_written(self):
        header_map, rows, _ = csv_io.parse_account_csv(csv_bytes('1101,Kas,1 - Asset'))
        clean, _ = csv_io.validate_rows(header_map, rows)
        for field in csv_io.BOOL_COLUMNS + ['description']:
            self.assertNotIn(field, clean[0]['values'], f'{field} tidak boleh ikut terisi')
        self.assertIsNone(clean[0]['currency_code'])
        self.assertIsNone(clean[0]['parent_code'])

    def test_self_parent_rejected(self):
        header_map, rows, _ = csv_io.parse_account_csv(csv_bytes('1101,Kas,1 - Asset,CASH,D,1101'))
        clean, errors = csv_io.validate_rows(header_map, rows)
        self.assertEqual(clean, [])
        self.assertIn('sama dengan kode akun sendiri', errors[0]['reason'])

    def test_row_above_limit_rejected(self):
        rows = '\r\n'.join(['1101,Kas,1 - Asset'] * (csv_io.MAX_ROWS + 1))
        _, _, errors = csv_io.parse_account_csv(BytesIO(f'{HEADER}\r\n{rows}\r\n'.encode()))
        self.assertEqual(len(errors), 1)
        self.assertIn('Maksimal', errors[0]['reason'])

    def test_detect_cycles_finds_two_node_loop(self):
        self.assertEqual(csv_io.detect_cycles({'1100': '1101', '1101': '1100'}), {'1100', '1101'})
        self.assertEqual(csv_io.detect_cycles({'1100': '1101', '1101': None}), set())


class AccountImportTestCase(TestCase):
    """Fase DB: plan + commit."""

    @classmethod
    def setUpTestData(cls):
        cls.company_a = cls._company('A', 'PT A')
        cls.company_b = cls._company('B', 'PT B')
        cls.user = User.objects.create_user('staff', password='x', is_staff=True)
        for company in (cls.company_a, cls.company_b):
            UserCompanyAccess.objects.create(user=cls.user, company=company)
        cls.idr = Currency.objects.create(code='IDR', name='Rupiah')

    @staticmethod
    def _company(code, name):
        group = Group.objects.create(name=f'Grup {code}', code=f'G{code}')
        return Company.objects.create(company_code=code, legal_name=name,
                                      display_name=name, group=group)

    def clean_rows(self, *rows):
        header_map, raw_rows, errors = csv_io.parse_account_csv(csv_bytes(*rows))
        self.assertEqual(errors, [])
        clean, errors = csv_io.validate_rows(header_map, raw_rows)
        self.assertEqual(errors, [], errors)
        return clean

    def test_create_update_and_unchanged_are_separated(self):
        Account.objects.create(company=self.company_a, account_code='1101',
                               account_name='Kas Lama', account_category=1,
                               account_type='CASH', normal_balance='D')
        rows = self.clean_rows(
            '1101,Kas Baru,1 - Asset,CASH,D',   # update
            '1102,Bank BCA,1 - Asset,BANK,D',   # create
        )
        plan = csv_io.build_plan(rows, self.company_a)

        self.assertEqual(plan['errors'], [])
        self.assertEqual([r['code'] for r in plan['create']], ['1102'])
        self.assertEqual([r['code'] for r in plan['update']], ['1101'])
        self.assertEqual(plan['unchanged'], [])
        self.assertEqual(plan['update'][0]['changes']['account_name'], ('Kas Lama', 'Kas Baru'))

        result = csv_io.commit_plan(plan, self.company_a, self.user)
        self.assertEqual(result, {'created': 1, 'updated': 1, 'unchanged': 0})

        updated = Account.objects.get(company=self.company_a, account_code='1101')
        self.assertEqual(updated.account_name, 'Kas Baru')
        self.assertEqual(updated.updated_by, self.user)
        created = Account.objects.get(company=self.company_a, account_code='1102')
        self.assertEqual(created.created_by, self.user)

    def test_reimport_same_file_is_a_noop(self):
        rows = self.clean_rows('1101,Kas,1 - Asset,CASH,D')
        csv_io.commit_plan(csv_io.build_plan(rows, self.company_a), self.company_a, self.user)
        second = csv_io.build_plan(rows, self.company_a)
        self.assertEqual(second['errors'], [])
        self.assertEqual(len(second['unchanged']), 1)
        self.assertEqual(second['create'], [])
        self.assertEqual(second['update'], [])
        self.assertEqual(
            csv_io.commit_plan(second, self.company_a, self.user),
            {'created': 0, 'updated': 0, 'unchanged': 1},
        )

    def test_blank_column_does_not_overwrite_existing_value(self):
        Account.objects.create(company=self.company_a, account_code='1101',
                               account_name='Kas', account_category=1,
                               account_type='CASH', normal_balance='D', is_postable=False)
        rows = self.clean_rows('1101,Kas,1 - Asset')
        plan = csv_io.build_plan(rows, self.company_a)
        csv_io.commit_plan(plan, self.company_a, self.user)
        acc = Account.objects.get(company=self.company_a, account_code='1101')
        self.assertFalse(acc.is_postable)
        self.assertEqual(acc.account_type, 'CASH')

    def test_parent_from_same_file_resolves_regardless_of_row_order(self):
        rows = self.clean_rows(
            '1101,Kas Tunai,1 - Asset,CASH,D,1100',  # anak lebih dulu
            '1100,Kas & Bank,1 - Asset,CASH_BANK,D',
        )
        plan = csv_io.build_plan(rows, self.company_a)
        self.assertEqual(plan['errors'], [])
        csv_io.commit_plan(plan, self.company_a, self.user)
        self.assertEqual(Account.objects.get(account_code='1101').parent.account_code, '1100')

    def test_parent_from_database_resolves(self):
        Account.objects.create(company=self.company_a, account_code='1100',
                               account_name='Kas & Bank', account_category=1,
                               account_type='CASH_BANK', normal_balance='D')
        rows = self.clean_rows('1101,Kas Tunai,1 - Asset,CASH,D,1100')
        plan = csv_io.build_plan(rows, self.company_a)
        self.assertEqual(plan['errors'], [])
        csv_io.commit_plan(plan, self.company_a, self.user)
        self.assertEqual(Account.objects.get(account_code='1101').parent.account_code, '1100')

    def test_unknown_parent_is_rejected(self):
        rows = self.clean_rows('1101,Kas Tunai,1 - Asset,CASH,D,9999')
        plan = csv_io.build_plan(rows, self.company_a)
        self.assertEqual(plan['errors'][0]['line'], 2)
        self.assertIn('9999', plan['errors'][0]['reason'])
        self.assertEqual(Account.objects.count(), 0)

    def test_parent_from_other_company_is_rejected(self):
        Account.objects.create(company=self.company_b, account_code='2200',
                               account_name='Utang', account_category=2,
                               account_type='LIABILITY', normal_balance='C')
        rows = self.clean_rows('2201,Utang Bank,2 - Liability,LIABILITY,C,2200')
        plan = csv_io.build_plan(rows, self.company_a)
        self.assertIn('milik company B', plan['errors'][0]['reason'])
        self.assertEqual(Account.objects.count(), 1, 'tidak boleh ada akun baru di company A')

    def test_cycle_is_rejected_and_nothing_is_written(self):
        rows = self.clean_rows(
            '1100,Kas & Bank,1 - Asset,CASH_BANK,D,1101',
            '1101,Kas Tunai,1 - Asset,CASH,D,1100',
        )
        plan = csv_io.build_plan(rows, self.company_a)
        self.assertTrue(any('siklus' in e['reason'] for e in plan['errors']))
        self.assertEqual(plan['create'], [])
        with self.assertRaises(ValueError):
            csv_io.commit_plan(plan, self.company_a, self.user)
        self.assertEqual(Account.objects.count(), 0)

    def test_unknown_currency_is_rejected(self):
        rows = self.clean_rows('1101,Kas,1 - Asset,CASH,D,,XYZ')
        plan = csv_io.build_plan(rows, self.company_a)
        self.assertIn('tidak dikenal', plan['errors'][0]['reason'])

    def test_commit_is_atomic(self):
        Account.objects.create(company=self.company_a, account_code='1100',
                               account_name='Kas & Bank', account_category=1,
                               account_type='CASH_BANK', normal_balance='D')
        rows = self.clean_rows(
            '1101,Kas Tunai,1 - Asset,CASH,D,1100',
            '1102,Bank,1 - Asset,BANK,D,9999',  # induk hilang -> plan error
        )
        plan = csv_io.build_plan(rows, self.company_a)
        self.assertTrue(plan['errors'])
        self.assertEqual(Account.objects.count(), 1, 'harus tetap 1, tidak ada akun baru')

    def test_company_scope_keeps_existing_accounts_apart(self):
        rows = self.clean_rows('1101,Kas,1 - Asset,CASH,D')
        csv_io.commit_plan(csv_io.build_plan(rows, self.company_a), self.company_a, self.user)
        # File yang sama ke company lain harus jadi akun baru, bukan update.
        plan_b = csv_io.build_plan(rows, self.company_b)
        self.assertEqual([r['code'] for r in plan_b['create']], ['1101'])
        self.assertEqual(plan_b['update'], [])

    def test_export_rows_roundtrip(self):
        parent = Account.objects.create(
            company=self.company_a, account_code='1100', account_name='Kas & Bank',
            account_category=1, account_type='CASH_BANK', normal_balance='D',
            currency=self.idr, is_postable=False,
        )
        Account.objects.create(
            company=self.company_a, account_code='1101', account_name='Kas Tunai',
            account_category=1, account_type='CASH', normal_balance='D',
            parent=parent, currency=self.idr,
        )
        exported = Account.objects.filter(company=self.company_a).select_related('parent', 'currency')
        rows = csv_io.export_rows(exported)
        self.assertEqual(rows[0], csv_io.COLUMNS)
        self.assertEqual(rows[1][csv_io.COLUMNS.index('account_code')], '1100')
        self.assertEqual(rows[2][csv_io.COLUMNS.index('parent_code')], '1100')
        self.assertEqual(rows[2][csv_io.COLUMNS.index('is_postable')], 'Y')
        self.assertEqual(rows[1][csv_io.COLUMNS.index('is_postable')], 'T')

        # Export -> import harus idempoten (tidak ada yang berubah).
        header_map, raw_rows, errors = csv_io.parse_account_csv(BytesIO(
            csv_io.render_csv(rows).encode('utf-8')))
        self.assertEqual(errors, [])
        clean, errors = csv_io.validate_rows(header_map, raw_rows)
        self.assertEqual(errors, [])
        plan = csv_io.build_plan(clean, self.company_a)
        self.assertEqual(plan['errors'], [])
        self.assertEqual(len(plan['unchanged']), 2, plan)


class AccountImportViewTests(TestCase):
    """Permission dan scope di level HTTP admin."""

    def setUp(self):
        self.company = self._company('A', 'PT A')
        self.other = self._company('B', 'PT B')
        self.staff = User.objects.create_user('staff', password='pw', is_staff=True)
        self.staff.user_permissions.add(*coa_perms('view_account', 'add_account', 'change_account'))
        UserCompanyAccess.objects.create(user=self.staff, company=self.company)

        # Staff tanpa izin akun apa pun: harus tetap 403, bukan 500 atau 200.
        self.nobody = User.objects.create_user('nobody', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.nobody, company=self.company)

        self.changelist = reverse('admin:gl_account_changelist')
        self.import_url = reverse('admin:gl_account_import_csv')
        self.template_url = reverse('admin:gl_account_template_csv')
        self.export_url = reverse('admin:gl_account_export_csv')

    @staticmethod
    def _company(code, name):
        group = Group.objects.create(name=f'Grup {code}', code=f'G{code}')
        return Company.objects.create(company_code=code, legal_name=name,
                                      display_name=name, group=group)

    def login(self, user=None):
        self.client.force_login(user or self.staff)

    def test_changelist_hides_accounts_of_other_company(self):
        Account.objects.create(company=self.other, account_code='9000', account_name='Rahasia',
                               account_category=1, account_type='X', normal_balance='D')
        self.login()
        response = self.client.get(self.changelist)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Rahasia')
        self.assertContains(response, reverse('admin:gl_account_import_csv'))

    def test_template_download_available_to_viewer(self):
        viewer = User.objects.create_user('viewer', password='pw', is_staff=True)
        viewer.user_permissions.add(*coa_perms('view_account'))
        UserCompanyAccess.objects.create(user=viewer, company=self.company)
        self.login(viewer)
        response = self.client.get(self.template_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        self.assertIn('account_code', response.content.decode('utf-8'))

    def test_template_download_blocked_without_permission(self):
        self.login(self.nobody)
        self.assertEqual(self.client.get(self.template_url).status_code, 403)

    def test_import_page_requires_view_permission(self):
        self.login(self.nobody)
        self.assertEqual(self.client.get(self.import_url).status_code, 403)

    def test_import_post_blocked_without_add_permission(self):
        viewer = User.objects.create_user('viewer2', password='pw', is_staff=True)
        viewer.user_permissions.add(*coa_perms('view_account'))
        UserCompanyAccess.objects.create(user=viewer, company=self.company)
        self.login(viewer)
        response = self.client.post(self.import_url, {'company_id': self.company.pk})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Account.objects.count(), 0)

    def test_company_outside_access_is_forbidden(self):
        self.login()
        response = self.client.get(f'{self.import_url}?company_id={self.other.pk}')
        self.assertEqual(response.status_code, 403)

    def test_import_creates_then_confirms(self):
        self.login()
        response = self.client.post(self.import_url, {
            'company_id': str(self.company.pk),
            'csv_file': _upload('1101,Kas Tunai,1 - Asset,CASH,D\n1102,Bank,1 - Asset,BANK,D\n'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Account.objects.count(), 0, 'preview tidak boleh menulis')
        self.assertContains(response, '2 akun baru')

        response = self.client.post(self.import_url, {
            'company_id': str(self.company.pk), 'confirm': '1',
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Account.objects.filter(company=self.company).count(), 2)

    def test_import_preview_shows_errors_and_blocks(self):
        self.login()
        response = self.client.post(self.import_url, {
            'company_id': str(self.company.pk),
            'csv_file': _upload('1101,Kas,1 - Asset,CASH,D,9999\n'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '9999')
        self.assertEqual(Account.objects.count(), 0)

    def test_import_rejects_cross_company_rows(self):
        Account.objects.create(company=self.other, account_code='9000', account_name='Lain',
                               account_category=1, account_type='X', normal_balance='D')
        self.login()
        response = self.client.post(self.import_url, {
            'company_id': str(self.company.pk),
            'csv_file': _upload('1101,Kas,1 - Asset,CASH,D,9000\n'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'milik company B')
        self.assertEqual(Account.objects.filter(company=self.company).count(), 0)

    def test_export_scoped_to_allowed_company(self):
        Account.objects.create(company=self.other, account_code='9000', account_name='Rahasia',
                               account_category=1, account_type='X', normal_balance='D')
        self.login()
        response = self.client.get(f'{self.export_url}?company_id={self.other.pk}')
        self.assertEqual(response.status_code, 403)
        response = self.client.get(f'{self.export_url}?company_id={self.company.pk}')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Rahasia', response.content.decode('utf-8'))

def coa_perms(*codenames):
    from django.contrib.auth.models import Permission

    return Permission.objects.filter(
        content_type__app_label='gl', content_type__model='account', codename__in=codenames,
    )


def _upload(text):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile('coa.csv', text.encode('utf-8'), content_type='text/csv')

class ClosingFlowTests(TestCase):
    """Alur tutup buku: bulanan (OPEN->CLOSING->CLOSED) + tahunan (jurnal penutup)."""

    def setUp(self):
        from core.models import Company, Currency, Group

        self.group = Group.objects.create(name='Grup Close', code='GCL')
        self.company = Company.objects.create(
            company_code='CL', legal_name='PT CL', display_name='PT CL', group=self.group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('closer', password='pw', is_staff=True)

        from .models import Account, AccountCategory, AccountingPeriod, FiscalYear

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Pendapatan',
            account_category=AccountCategory.REVENUE, account_type='PRODUCT_REVENUE',
            normal_balance='C', is_postable=True)
        self.expense = Account.objects.create(
            company=self.company, account_code='6101', account_name='Beban',
            account_category=AccountCategory.EXPENSE, account_type='GENERAL_EXPENSE',
            normal_balance='D', is_postable=True)
        self.retained = Account.objects.create(
            company=self.company, account_code='3201', account_name='Laba Ditahan',
            account_category=AccountCategory.EQUITY, account_type='RETAINED_EARNINGS',
            normal_balance='C', is_postable=True)

        self.fiscal_year = FiscalYear.objects.create(
            company=self.company, name='2026',
            start_date='2026-01-01', end_date='2026-12-31')
        self.period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=self.fiscal_year, period_code='2026-01',
            period_name='Januari 2026', start_date='2026-01-01', end_date='2026-01-31')
        # Jurnal penutup bertanggal akhir fiscal year, jadi period terakhir harus ada.
        self.dec_period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=self.fiscal_year, period_code='2026-12',
            period_name='Desember 2026', start_date='2026-12-01', end_date='2026-12-31')

    def _post_journal(self, date, lines, reference=''):
        from decimal import Decimal

        from .engine import JournalBuilder
        from .models import JournalSource

        builder = JournalBuilder(self.company, date, source=JournalSource.MANUAL,
                                 description='uji closing', reference=reference, user=self.user)
        for account, side, amount in lines:
            getattr(builder, 'debit' if side == 'D' else 'credit')(account, Decimal(amount))
        return builder.save(auto_post=True)

    def test_alur_bulanan_dan_checklist(self):
        from .models import JournalStatus, PeriodStatus
        from .services import close_period, period_closing_checklist, start_closing

        # Jurnal draft menghalangi mulai closing.
        from .engine import JournalBuilder
        from .models import JournalSource

        draft = JournalBuilder(self.company, '2026-01-10', source=JournalSource.MANUAL,
                               description='draft').debit(self.expense, 100).credit(self.cash, 100)
        journal = draft.save(status=JournalStatus.DRAFT)
        with self.assertRaises(ValidationError):
            start_closing(self.period, self.user)

        # Setelah draft diposting, checklist bersih -> CLOSING -> CLOSED.
        journal.post(self.user)
        checklist = period_closing_checklist(self.period)
        self.assertTrue(all(item['done'] for item in checklist if item['blocking']))

        start_closing(self.period, self.user)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, PeriodStatus.CLOSING)
        self.assertEqual(self.period.closing_by, self.user)

        close_period(self.period, self.user)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, PeriodStatus.CLOSED)

    def test_tutup_tahun_buat_jurnal_penutup(self):
        from decimal import Decimal

        from .models import JournalEntry, JournalSource, JournalStatus
        from .services import close_fiscal_year, close_period

        self._post_journal('2026-01-15', [(self.cash, 'D', '1000'), (self.revenue, 'C', '1000')])
        self._post_journal('2026-01-16', [(self.expense, 'D', '400'), (self.cash, 'C', '400')])
        close_period(self.period, self.user)
        close_period(self.dec_period, self.user)

        close_fiscal_year(self.fiscal_year, user=self.user)

        self.fiscal_year.refresh_from_db()
        self.assertTrue(self.fiscal_year.is_closed)
        self.assertEqual(self.fiscal_year.closed_by, self.user)

        closing = JournalEntry.objects.get(source=JournalSource.CLOSING, reference='YE/2026')
        self.assertEqual(closing.status, JournalStatus.POSTED)
        movement = {
            line.account_id: (line.debit or Decimal('0')) - (line.credit or Decimal('0'))
            for line in closing.lines.all()
        }
        # Pendapatan didebit 1000, beban dikredit 400, laba 600 ke Laba Ditahan.
        self.assertEqual(movement[self.revenue.pk], Decimal('1000'))
        self.assertEqual(movement[self.expense.pk], Decimal('-400'))
        self.assertEqual(movement[self.retained.pk], Decimal('-600'))
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'CLOSED')

        # Idempotent: panggil ulang tidak menambah jurnal penutup.
        close_fiscal_year(self.fiscal_year, user=self.user)
        self.assertEqual(
            JournalEntry.objects.filter(source=JournalSource.CLOSING).count(), 1)

    def test_reopen_ditolak_setelah_tahun_ditutup(self):
        from .services import close_fiscal_year, close_period, reopen_period

        close_period(self.period, self.user)
        close_period(self.dec_period, self.user)
        close_fiscal_year(self.fiscal_year, user=self.user)
        with self.assertRaises(ValidationError):
            reopen_period(self.period, self.user)


class ReopenFiscalYearTests(TestCase):
    """Buka tutup buku tahunan: reversal jurnal penutup + lepas kunci."""

    def setUp(self):
        from core.models import Company, Currency, Group

        group = Group.objects.create(name='Grup RF', code='GRF')
        self.company = Company.objects.create(
            company_code='RF', legal_name='PT RF', display_name='PT RF', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('rf', password='pw', is_staff=True)

        from .models import Account, AccountCategory, AccountingPeriod, FiscalYear

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Pendapatan',
            account_category=AccountCategory.REVENUE, account_type='PRODUCT_REVENUE',
            normal_balance='C', is_postable=True)
        self.retained = Account.objects.create(
            company=self.company, account_code='3201', account_name='Laba Ditahan',
            account_category=AccountCategory.EQUITY, account_type='RETAINED_EARNINGS',
            normal_balance='C', is_postable=True)

        self.fiscal_year = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        self.period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=self.fiscal_year, period_code='2026-12',
            period_name='Desember 2026', start_date='2026-12-01', end_date='2026-12-31')

        from .engine import JournalBuilder
        from .models import JournalSource

        JournalBuilder(self.company, '2026-12-15', source=JournalSource.MANUAL,
                       description='pendapatan').debit(self.cash, 500).credit(
                           self.revenue, 500).save(auto_post=True)

    def test_reopen_membalik_jurnal_penutup_dan_melepas_kunci(self):
        from .models import JournalEntry, JournalSource
        from .services import (
            close_fiscal_year, close_period, reopen_fiscal_year, start_closing,
        )

        close_period(self.period, self.user)
        close_fiscal_year(self.fiscal_year, user=self.user)
        closing = JournalEntry.objects.get(source=JournalSource.CLOSING)
        self.assertTrue(closing.is_reversed is False)

        reopen_fiscal_year(self.fiscal_year, user=self.user)

        self.fiscal_year.refresh_from_db()
        self.assertFalse(self.fiscal_year.is_closed)
        self.assertIsNone(self.fiscal_year.closed_by)
        closing.refresh_from_db()
        self.assertTrue(closing.is_reversed, 'jurnal penutup harus ditandai reversed')
        self.assertEqual(
            JournalEntry.objects.filter(reversed_from=closing).count(), 1,
            'harus ada satu jurnal reversal')

        # Setelah dibuka, closing bulanan bisa dijalankan lagi (refresh dulu:
        # objek self.period masih memegang status lama di memori).
        self.period.refresh_from_db()
        start_closing(self.period, self.user)
        self.period.refresh_from_db()
        self.assertEqual(self.period.status, 'CLOSING')


class FiscalYearFormReadonlyTests(TestCase):
    """is_closed tidak boleh dicentang manual dari form admin."""

    def setUp(self):
        from core.models import Company, Group

        group = Group.objects.create(name='Grup FR', code='GFR')
        self.company = Company.objects.create(
            company_code='FR', legal_name='PT FR', display_name='PT FR', group=group)
        self.user = User.objects.create_superuser('fr_admin', password='pw')
        self.client.force_login(self.user)

    def test_is_closed_readonly_di_form(self):
        from .models import FiscalYear

        fy = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        html = self.client.get(f'/admin/gl/fiscalyear/{fy.pk}/change/').content.decode()
        self.assertNotIn('name="is_closed"', html)
        self.assertIn('readonly', html)

        # Action tutup/buka buku hanya tersedia dari changelist.
        listing = self.client.get('/admin/gl/fiscalyear/').content.decode()
        self.assertIn('Buka kembali tutup buku tahunan', listing)
        self.assertIn('Tutup buku tahunan', listing)


class OpeningBalanceTests(TestCase):
    """Jurnal saldo awal dari CSV (migrasi dari sistem lama)."""

    def setUp(self):
        from core.models import Company, Currency, Group

        group = Group.objects.create(name='Grup OB', code='GOB')
        self.company = Company.objects.create(
            company_code='OB', legal_name='PT OB', display_name='PT OB', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('ob', password='pw', is_staff=True)

        from .models import Account, AccountCategory, AccountingPeriod, FiscalYear

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.capital = Account.objects.create(
            company=self.company, account_code='3101', account_name='Modal',
            account_category=AccountCategory.EQUITY, account_type='PAID_IN_CAPITAL',
            normal_balance='C', is_postable=True)
        fy = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fy, period_code='2026-01',
            period_name='Januari 2026', start_date='2026-01-01', end_date='2026-01-31')

    def _write_csv(self, text):
        import tempfile
        handle = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8')
        handle.write(text)
        handle.close()
        self.addCleanup(lambda: __import__('os').unlink(handle.name))
        return handle.name

    def test_service_membuat_jurnal_balance(self):
        from decimal import Decimal

        from .models import JournalEntry, JournalSource
        from .services import post_opening_balance

        journal = post_opening_balance(
            self.company, '2026-01-01',
            [(self.cash, Decimal('5000000'), 0), (self.capital, 0, Decimal('5000000'))])
        self.assertEqual(journal.source, JournalSource.OPENING_BALANCE)
        self.assertEqual(journal.total_debit, Decimal('5000000'))
        self.assertTrue(JournalEntry.objects.filter(pk=journal.pk).exists())

    def test_service_menolak_tidak_balance_dan_akun_lain(self):
        from decimal import Decimal

        from core.models import Company, Group
        from .services import post_opening_balance

        with self.assertRaises(ValidationError):
            post_opening_balance(self.company, '2026-01-01',
                                 [(self.cash, Decimal('100'), 0)])

        other = Company.objects.create(
            company_code='OB2', legal_name='PT OB2', display_name='PT OB2',
            group=Group.objects.get(code='GOB'))
        other_account = self.cash.__class__.objects.create(
            company=other, account_code='1101', account_name='Kas lain',
            account_category=1, account_type='CASH', normal_balance='D', is_postable=True)
        with self.assertRaises(ValidationError):
            post_opening_balance(self.company, '2026-01-01',
                                 [(other_account, Decimal('1'), 0), (self.capital, 0, Decimal('1'))])

    def test_command_dry_run_tidak_menulis(self):
        from io import StringIO

        from django.core.management import call_command
        from .models import JournalEntry

        path = self._write_csv(
            'kode_akun,debit,kredit\n1101,"1.000.000,00",\n3101,,"1.000.000,00"\n')
        out = StringIO()
        call_command('load_opening_balance', company='OB', date='2026-01-01',
                     file=path, dry_run=True, stdout=out, stderr=StringIO())
        self.assertIn('debit 1000000.00', out.getvalue())
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_command_membuat_jurnal_dan_idempotent(self):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError
        from .models import JournalEntry

        path = self._write_csv(
            'account_code,debit,credit\n1101,2000000,\n3101,,2000000\n')
        call_command('load_opening_balance', company='OB', date='2026-01-01', file=path,
                     stdout=StringIO(), stderr=StringIO())
        self.assertEqual(JournalEntry.objects.count(), 1)

        # Reference default sama -> ditolak, tidak dobel.
        with self.assertRaises(CommandError):
            call_command('load_opening_balance', company='OB', date='2026-01-01', file=path,
                         stdout=StringIO(), stderr=StringIO())

    def test_command_menolak_akun_tidak_dikenal(self):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError
        from .models import JournalEntry

        path = self._write_csv('kode_akun,debit,kredit\n9999,100,\n1101,,100\n')
        with self.assertRaises(CommandError):
            call_command('load_opening_balance', company='OB', date='2026-01-01', file=path,
                         stdout=StringIO(), stderr=StringIO())
        self.assertEqual(JournalEntry.objects.count(), 0)


class OpeningBalanceReviewTests(TestCase):
    """Regresi dari review: nilai negatif, dry-run tidak balance, period hilang."""

    def setUp(self):
        from core.models import Company, Currency, Group

        group = Group.objects.create(name='Grup OBR', code='GOBR')
        self.company = Company.objects.create(
            company_code='OBR', legal_name='PT OBR', display_name='PT OBR', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        from .models import Account, AccountCategory

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.capital = Account.objects.create(
            company=self.company, account_code='3101', account_name='Modal',
            account_category=AccountCategory.EQUITY, account_type='PAID_IN_CAPITAL',
            normal_balance='C', is_postable=True)

    def _csv(self, text):
        import tempfile
        handle = tempfile.NamedTemporaryFile('w', suffix='.csv', delete=False, encoding='utf-8')
        handle.write(text); handle.close()
        self.addCleanup(lambda: __import__('os').unlink(handle.name))
        return handle.name

    def test_service_menolak_nilai_negatif(self):
        from decimal import Decimal

        from .services import post_opening_balance

        with self.assertRaises(ValidationError):
            post_opening_balance(self.company, '2026-01-01',
                                 [(self.cash, Decimal('-100'), 0), (self.capital, 0, Decimal('-100'))])

    def test_dry_run_menolak_tidak_balance_tanpa_menulis(self):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError
        from .models import JournalEntry

        path = self._csv('kode_akun,debit,kredit\n1101,100,\n3101,,50\n')
        with self.assertRaises(CommandError):
            call_command('load_opening_balance', company='OBR', date='2026-01-01',
                         file=path, dry_run=True, stdout=StringIO(), stderr=StringIO())
        self.assertEqual(JournalEntry.objects.count(), 0)

    def test_period_hilang_dilaporkan_sebagai_command_error(self):
        from io import StringIO

        from django.core.management import call_command
        from django.core.management.base import CommandError

        # Tanpa fiscal year / period sama sekali.
        path = self._csv('kode_akun,debit,kredit\n1101,100,\n3101,,100\n')
        with self.assertRaises(CommandError):
            call_command('load_opening_balance', company='OBR', date='2026-01-01',
                         file=path, stdout=StringIO(), stderr=StringIO())


class OpeningBalanceAdminViewTests(TestCase):
    """Halaman admin input saldo awal: preview lalu posting."""

    def setUp(self):
        from core.models import Company, Currency, Group

        group = Group.objects.create(name='Grup OBV', code='GOBV')
        self.company = Company.objects.create(
            company_code='OBV', legal_name='PT OBV', display_name='PT OBV', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        from .models import Account, AccountCategory, AccountingPeriod, FiscalYear

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.capital = Account.objects.create(
            company=self.company, account_code='3101', account_name='Modal',
            account_category=AccountCategory.EQUITY, account_type='PAID_IN_CAPITAL',
            normal_balance='C', is_postable=True)
        fy = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fy, period_code='2026-01',
            period_name='Januari 2026', start_date='2026-01-01', end_date='2026-01-31')

        self.user = User.objects.create_user('obv', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.user.user_permissions.add(*Permission.objects.filter(
            codename__in=['view_accountingperiod', 'change_accountingperiod']))
        self.client.force_login(self.user)
        self.url = '/admin/gl/accountingperiod/opening-balance/'

    def _upload(self, text):
        from django.core.files.uploadedfile import SimpleUploadedFile
        return SimpleUploadedFile('ob.csv', text.encode('utf-8'), content_type='text/csv')

    def test_preview_lalu_posting(self):
        from .models import JournalEntry, JournalSource

        response = self.client.post(self.url, {
            'company': self.company.pk, 'as_of': '2026-01-01',
            'csv_file': self._upload('kode_akun,debit,kredit\n1101,"1.000.000",\n3101,,"1.000.000"\n'),
        })
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn('Pratinjau', html)          # label diterjemahkan (id)
        self.assertIn('1101', html)
        self.assertIn('3101', html)
        self.assertIn('name="confirm"', html)
        self.assertEqual(JournalEntry.objects.count(), 0, 'preview belum menulis')

        response = self.client.post(self.url, {
            'company': self.company.pk, 'as_of': '2026-01-01', 'confirm': '1',
        })
        self.assertEqual(response.status_code, 302)
        journal = JournalEntry.objects.get(source=JournalSource.OPENING_BALANCE)
        self.assertEqual(journal.reference, 'OB/2026-01-01')
        self.assertEqual(journal.total_debit, journal.total_credit)

    def test_judul_tidak_dobel_dan_tombol_ada(self):
        import re

        html = self.client.get(self.url, {'company': self.company.pk}).content.decode()
        headings = re.findall(r'<h1[^>]*>([^<]*)</h1>', html)
        self.assertEqual([h.strip() for h in headings].count('Input Saldo Awal'), 1,
                         f'judul halaman harus satu, dapat {headings}')
        self.assertIn('opening-balance-actions', html)

    def test_unduh_template_csv(self):
        response = self.client.get(self.url, {'company': self.company.pk, 'template': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        body = response.content.decode('utf-8-sig')
        self.assertTrue(body.startswith('kode_akun,nama_akun,debit,kredit'))
        self.assertIn('1101,Kas', body)
        self.assertIn('3101,Modal', body)

class ManualJournalAdminTests(TestCase):
    """Input jurnal umum lewat admin: periode & nomor otomatis, kategori tersimpan."""

    def setUp(self):
        from gl.models import Account, AccountCategory, AccountingPeriod, FiscalYear

        group = Group.objects.create(name='Grup MJ', code='GMJ')
        self.company = Company.objects.create(
            company_code='MJ', legal_name='PT MJ', display_name='PT MJ', group=group)
        self.currency = Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_superuser('mj_admin', password='pw')
        self.client.force_login(self.user)

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Pendapatan',
            account_category=AccountCategory.REVENUE, account_type='PRODUCT_REVENUE',
            normal_balance='C', is_postable=True)
        fy = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        self.period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fy, period_code='2026-01',
            period_name='Januari 2026', start_date='2026-01-01', end_date='2026-01-31')

    def test_tambah_jurnal_kategori_pajak(self):
        from gl.models import JournalEntry, JournalStatus

        url = reverse('admin:gl_journalentry_add')
        self.assertEqual(self.client.get(url).status_code, 200)

        data = {
            'company': str(self.company.pk),
            'journal_date': '2026-01-10',
            'source': 'MANUAL',
            'category': 'TAX',
            'currency': str(self.currency.pk),
            'exchange_rate': '1.00000000',
            'description': 'Jurnal pajak manual',
            'reference': 'PJ-001',
            'lines-TOTAL_FORMS': '2', 'lines-INITIAL_FORMS': '0',
            'lines-MIN_NUM_FORMS': '0', 'lines-MAX_NUM_FORMS': '1000',
            'lines-0-line_no': '10', 'lines-0-account': str(self.cash.pk),
            'lines-0-debit': '100', 'lines-0-credit': '0',
            'lines-1-line_no': '20', 'lines-1-account': str(self.revenue.pk),
            'lines-1-debit': '0', 'lines-1-credit': '100',
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content.decode()[:500])

        journal = JournalEntry.objects.get(reference='PJ-001')
        self.assertEqual(journal.category, 'TAX')
        self.assertEqual(journal.status, JournalStatus.DRAFT)
        self.assertEqual(journal.period, self.period)
        self.assertEqual(journal.journal_number, 'JE/2026-01/00001')
        self.assertEqual(journal.lines.count(), 2)
        self.assertTrue(journal.is_balanced)


