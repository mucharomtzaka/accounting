"""Guard regresi korupssi markup di template.

Latar: pola `</h2> 'Revenue' %}</h2>` / `</th>ate 'Tipe' %}</th>` pernah muncul
di belasan template report. Gejalanya subtle - browser melakukan auto-correction
saat parsing sehingga halaman tetap render 200 dan `manage.py check` tetap hijau,
tapi teks sisa tag bocor ke layar user.

Dua lapis deteksi:

1. Scan sumber template (statis) - menangkap pola fragmen nyasar dan tag yang
   tidak seimbang, tanpa butuh data.
2. Render tiap template lalu cari `{%` / `%}` sebagai TEKS LITERAL di output.
   Di halaman yang sehat string itu tidak mungkin muncul karena tag sudah
   dikonsumsi engine template. Lapis ini menangkap kelas kerusakan yang tidak
   terlihat di sumber.

Keduanya sengaja tanpa DB (SimpleTestCase): korupssi markup adalah cacat sumber
template, tidak bergantung data, jadi test tidak perlu seed.
"""

import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from core.models import Company, Currency, Group, User, UserCompanyAccess

REPORT_TEMPLATES = sorted(
    p.name for p in (Path(settings.BASE_DIR) / 'templates' / 'reports').glob('*.html')
    if p.name != 'base.html'
)

TAG_NAME = r'[a-zA-Z][a-zA-Z0-9]*'
# Fragmen nyasar: tag penutup diikuti sisa tag Django, lalu tag penutup yang sama.
# `mid`Vc42W tidak boleh mengandung '%' supaya `</span>{% translate 'X' %}</span>`
# yang sah (tag Django di antara dua tag penutup) tidak ikut kena.
STRAY_FRAGMENT = re.compile(
    rf'</(?P<tag>{TAG_NAME})>(?P<mid>[^<%\n]*?)'
    rf"(?P<stray>[a-z]{{0,3}}\s*'[A-Za-z][A-Za-z0-9 _/&]*'\s*%\}})"
    rf'\s*</(?P=tag)>'
)
VOID_TAGS = {
    'br', 'hr', 'img', 'input', 'meta', 'link', 'area', 'base', 'col',
    'embed', 'source', 'track', 'wbr',
}


def _tag_balance_problems(text):
    """Cek keseimbangan tag HTML. Return list pesan."""
    problems = []
    # Komentar Django dibuang dulu supaya tag di dalamnya tidak dihitung.
    text = re.sub(r'{%\s*comment\s*%}.*?{%\s*endcomment\s*%}', '', text, flags=re.S)
    text = re.sub(r'{%\s*blocktrans.*?%}.*?{%\s*endblocktrans\s*%}', '', text, flags=re.S)
    stack = []
    for match in re.finditer(rf'<(/?)({TAG_NAME})((?:"[^"]*"|\'[^\']*\'|[^>"\'])*?)(/?)>', text):
        closing, name, self_closing = match.group(1), match.group(2).lower(), match.group(4)
        line = text[:match.start()].count('\n') + 1
        if name in VOID_TAGS or self_closing:
            continue
        if not closing:
            stack.append((name, line))
        elif stack and stack[-1][0] == name:
            stack.pop()
        elif any(entry[0] == name for entry in stack):
            problems.append(f'baris {line}: </{name}> ditutup di luar pas')
        else:
            problems.append(f'baris {line}: </{name}> tanpa tag pembuka')
    for name, line in stack:
        problems.append(f'baris {line}: <{name}> tidak pernah ditutup')
    return problems


class _Stub:
    """Nilai placeholder supaya branch `{% if data %}` tetap ikut ter-render.

    Menarik: korupssi markup sering berada di dalam branch yang butuh data,
    jadi render tanpa stub akan melewatinya dan test jadi buta. Atribut apa pun
    dikembalikan sebagai Stub lagi, iterasi kosong, dan `str()` kosong -
    filter `money` sudah menangani nilai tak-konversi dan mengembalikan inputnya.
    """

    # Atribut tanggal harus angka asli: __getattr__ di bawah akan mengembalikan
    # stub untuk day/month/year, lalu date_format gagal dengan "%d format".
    day = 1
    month = 1
    year = 2025
    hour = 0
    minute = 0
    second = 0
    microsecond = 0

    def __getattr__(self, name):
        return self

    def __getitem__(self, key):
        return self

    def __str__(self):
        return ''

    def __iter__(self):
        return iter(())

    def __len__(self):
        return 0

    def __bool__(self):
        return True


class TemplateSourceTests(SimpleTestCase):
    """Lapisan 1: periksa sumber seluruh template di templates/."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.templates = sorted(Path(settings.BASE_DIR).rglob('templates/**/*.html'))

    def test_ada_template_yang_diaudit(self):
        # Jaga agar scan tidak diam-diam jadi no-op kalau path berubah.
        self.assertGreater(len(self.templates), 5)

    def test_tidak_ada_fragmen_tag_nyasar(self):
        offenders = []
        for path in self.templates:
            for match in STRAY_FRAGMENT.finditer(path.read_text()):
                line = path.read_text()[:match.start()].count('\n') + 1
                offenders.append(
                    f'{path.relative_to(settings.BASE_DIR)}:{line} {match.group(0).strip()!r}'
                )
        self.assertEqual(
            offenders, [],
            'Terdapat fragmen sisa tag di template (contoh: </h2> \'X\' %}</h2>):\n  '
            + '\n  '.join(offenders),
        )

    def test_tag_html_seimbang(self):
        offenders = []
        for path in self.templates:
            for problem in _tag_balance_problems(path.read_text()):
                offenders.append(f'{path.relative_to(settings.BASE_DIR)}: {problem}')
        self.assertEqual(
            offenders, [],
            'Tag HTML tidak seimbang:\n  ' + '\n  '.join(offenders),
        )


class RenderedTemplateTests(SimpleTestCase):
    """Lapisan 2: render tiap template report, cari tag Django literal."""

    # Stub generik: mencakup nama variabel yang dipakai template report.
    # `companies`/`rows` sengaja dibiarkan truthy agar branch terisi.
    BASE_CONTEXT = {
        'csrf_token': 'stub',
        'start': '2025-01-01', 'end': '2025-12-31', 'as_of': '2025-12-31',
        'company': 'A', 'account': 1, 'customer': 1, 'supplier': 1,
        'is_balanced': True, 'closing_balance': None,
        'total_debit': None, 'total_credit': None,
        'ar_total': None, 'ap_total': None,
        'group_currency': 'IDR',
        'all_companies': [], 'companies': [], 'accounts': [], 'customers': [],
        'suppliers': [], 'rows': [_Stub()], 'data': _Stub(), 'totals': _Stub(),
        'dimension': 'customer', 'budget_type': 'MONTHLY',
        'draft_invoices': 0, 'draft_bills': 0, 'draft_journals': 0,
        'ar_rows': [], 'ar_months': [], 'pnl': _Stub(),
        'balance_sheet': _Stub(), 'cash_flow': _Stub(),
        # Dipakai templates/reports/_doc_header.html (kop company + tombol print).
        'active_company': _Stub(), 'user': _Stub(),
    }

    def test_semua_template_report_teraudit(self):
        self.assertGreaterEqual(len(REPORT_TEMPLATES), 10)

    def test_tidak_ada_tag_django_literal_di_hasil_render(self):
        offenders = []
        for name in REPORT_TEMPLATES:
            html = get_template(f'reports/{name}').render(dict(self.BASE_CONTEXT))
            for literal in ('{%', '%}'):
                if literal in html:
                    index = html.index(literal)
                    snippet = html[max(0, index - 45):index + 30].strip()
                    offenders.append(f'reports/{name}: {literal!r} -> ...{snippet}...')
        self.assertEqual(
            offenders, [],
            'Tag Django bocor jadi teks di hasil render:\n  ' + '\n  '.join(offenders),
        )

    def test_render_tidak_melempar_exception(self):
        for name in REPORT_TEMPLATES:
            with self.subTest(template=name):
                get_template(f'reports/{name}').render(dict(self.BASE_CONTEXT))

class ReportDocumentToolsTests(TestCase):
    """Kop company + tombol print/export ada di SETIAP halaman report.

    Export & print diambil dari reports/base.html, jadi cukup satu guard di
    sini: modul report baru yang pakai base itu ikut otomatis.
    """

    URLS = [
        'reports_dashboard', 'reports_trial_balance', 'reports_profit_loss',
        'reports_balance_sheet', 'reports_cash_flow', 'reports_general_ledger',
        'reports_aging', 'reports_company_comparison', 'reports_consolidated',
        'reports_project_profitability', 'reports_budget_vs_actual',
        'reports_cost_accounting', 'reports_product_profitability',
        'reports_customer_statement', 'reports_vendor_statement',
        'reports_journal_by_category',
    ]

    def setUp(self):
        group = Group.objects.create(name='Grup A', code='GA')
        self.company = Company.objects.create(
            company_code='A', legal_name='PT A', display_name='PT A', group=group, nib='1.2.3.4')
        self.user = User.objects.create_user('tooluser', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.client.force_login(self.user)

    def test_kop_dan_tombol_ada_di_semua_halaman(self):
        for name in self.URLS:
            with self.subTest(report=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                html = response.content.decode()
                self.assertIn('id="report-doc-header"', html)
                self.assertIn('report-print-btn', html)
                self.assertIn('report-export-btn', html)
                self.assertIn('js/report-tools.js', html)
                self.assertIn('css/report-print.css', html)

    def test_kop_memuat_kode_dan_nama_company_aktif(self):
        html = self.client.get(reverse('reports_trial_balance')).content.decode()
        self.assertIn('data-company="A"', html)
        self.assertIn('PT A', html)
        self.assertIn('data-company-nib="1.2.3.4"', html)
        self.assertIn('data-user="tooluser"', html)


class CashFlowDetailTests(TestCase):
    """Cash flow harus punya rincian baris kas, bukan cuma KPI."""

    def setUp(self):
        group = Group.objects.create(name='Grup CF', code='GCF')
        self.company = Company.objects.create(
            company_code='CF', legal_name='PT CF', display_name='PT CF', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('cf', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.client.force_login(self.user)

    def test_rincian_muncul_dan_terklasifikasi(self):
        from decimal import Decimal

        from gl.models import (
            Account, AccountCategory, AccountingPeriod, FiscalYear, JournalEntry,
            JournalLine, JournalStatus,
        )

        bank = Account.objects.create(
            company=self.company, account_code='1102', account_name='Bank BCA',
            account_category=AccountCategory.ASSET, account_type='BANK',
            normal_balance='D', is_postable=True)
        revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Penjualan',
            account_category=AccountCategory.REVENUE, account_type='PRODUCT_REVENUE',
            normal_balance='C', is_postable=True)
        fiscal_year = FiscalYear.objects.create(
            company=self.company, name='2026',
            start_date='2026-01-01', end_date='2026-12-31')
        period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fiscal_year, period_code='2026-10',
            period_name='Oktober 2026', start_date='2026-10-01', end_date='2026-10-31')
        journal = JournalEntry.objects.create(
            company=self.company, period=period,
            journal_number='CF-001', journal_date='2026-10-05',
            source='MANUAL', status=JournalStatus.POSTED)
        JournalLine.objects.create(journal=journal, line_no=10, account=bank,
                                   debit=Decimal('1000'), credit=Decimal('0'))
        JournalLine.objects.create(journal=journal, line_no=20, account=revenue,
                                   debit=Decimal('0'), credit=Decimal('1000'))

        from core.models import Company as _Company
        from reports import engine
        data = engine.cash_flow(_Company.objects.filter(pk=self.company.pk), '2026-10-01', '2026-10-31')
        operating = next(s for s in data['sections'] if s['key'] == 'Operating')
        self.assertEqual(len(operating['lines']), 1)
        self.assertEqual(operating['inflow'], Decimal('1000'))
        self.assertEqual(data['net_cash'], Decimal('1000'))
        self.assertTrue(data['has_activity'])

        html = self.client.get(reverse('reports_cash_flow') + '?start=2026-10-01&end=2026-10-31').content.decode()
        self.assertIn('Bank BCA', html)
        self.assertIn('CF-001', html)
        self.assertIn('Operating', html)


class CashFlowReconciliationTests(TestCase):
    """Transfer antar kas tampil gross tapi tidak ikut net; ada kas awal/akhir."""

    def setUp(self):
        group = Group.objects.create(name='Grup CF2', code='GCF2')
        self.company = Company.objects.create(
            company_code='CF2', legal_name='PT CF2', display_name='PT CF2', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('cf2', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.client.force_login(self.user)

        from gl.models import Account, AccountCategory, AccountingPeriod, FiscalYear

        self.bank = Account.objects.create(
            company=self.company, account_code='1102', account_name='Bank BCA',
            account_category=AccountCategory.ASSET, account_type='BANK',
            normal_balance='D', is_postable=True)
        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas Tunai',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Penjualan',
            account_category=AccountCategory.REVENUE, account_type='PRODUCT_REVENUE',
            normal_balance='C', is_postable=True)
        fy = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fy, period_code='2026-09',
            period_name='September 2026', start_date='2026-09-01', end_date='2026-09-30')
        self.period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fy, period_code='2026-10',
            period_name='Oktober 2026', start_date='2026-10-01', end_date='2026-10-31')

    def _journal(self, number, date, lines):
        from gl.models import AccountingPeriod, JournalEntry, JournalLine, JournalStatus
        period = (self.period if date.startswith('2026-10')
                  else AccountingPeriod.objects.get(company=self.company, period_code='2026-09'))
        journal = JournalEntry.objects.create(
            company=self.company, period=period, journal_number=number, journal_date=date,
            source='MANUAL', status=JournalStatus.POSTED)
        for line_no, (account, debit, credit) in enumerate(lines, start=1):
            JournalLine.objects.create(journal=journal, line_no=line_no * 10, account=account,
                                       debit=Decimal(debit), credit=Decimal(credit))
        return journal

    def test_kas_awal_akhir_dan_transfer_gross(self):
        from decimal import Decimal as D

        from reports import engine

        # Periode lalu: kas masuk 500 -> jadi kas awal Oktober.
        self._journal('CF2-000', '2026-09-20', [(self.bank, '500', '0'), (self.revenue, '0', '500')])
        # Oktober: penjualan 1000 ke bank, lalu transfer bank -> kas tunai 200.
        self._journal('CF2-001', '2026-10-05', [(self.bank, '1000', '0'), (self.revenue, '0', '1000')])
        self._journal('CF2-002', '2026-10-06', [(self.cash, '200', '0'), (self.bank, '0', '200')])

        data = engine.cash_flow(Company.objects.filter(pk=self.company.pk), '2026-10-01', '2026-10-31')

        self.assertEqual(data['opening_cash'], D('500'))
        self.assertEqual(data['net_cash'], D('1000'), 'transfer tidak menambah net cash')
        self.assertEqual(data['closing_cash'], D('1500'))
        self.assertEqual(data['transfer']['inflow'], D('200'))
        self.assertEqual(data['transfer']['outflow'], D('200'))

        transfer = next(s for s in data['sections'] if s['key'] == 'Transfer')
        self.assertEqual(len(transfer['lines']), 2)

        html = self.client.get(
            reverse('reports_cash_flow') + '?start=2026-10-01&end=2026-10-31').content.decode()
        self.assertIn('Ringkasan Arus Kas', html)
        self.assertIn('Transfer antar kas', html)


class DashboardPeriodFilterTests(TestCase):
    """Dashboard mengikuti filter periode (start/end)."""

    def setUp(self):
        group = Group.objects.create(name='Grup DB', code='GDB')
        self.company = Company.objects.create(
            company_code='DB', legal_name='PT DB', display_name='PT DB', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('db', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.client.force_login(self.user)

        from gl.models import (
            Account, AccountCategory, AccountingPeriod, FiscalYear, JournalEntry,
            JournalLine, JournalStatus,
        )
        revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Penjualan',
            account_category=AccountCategory.REVENUE, account_type='PRODUCT_REVENUE',
            normal_balance='C', is_postable=True)
        bank = Account.objects.create(
            company=self.company, account_code='1102', account_name='Bank',
            account_category=AccountCategory.ASSET, account_type='BANK',
            normal_balance='D', is_postable=True)
        fy = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fy, period_code='2026-10',
            period_name='Oktober 2026', start_date='2026-10-01', end_date='2026-10-31')
        journal = JournalEntry.objects.create(
            company=self.company, period=period, journal_number='DB-001',
            journal_date='2026-10-15', source='MANUAL', status=JournalStatus.POSTED)
        JournalLine.objects.create(journal=journal, line_no=10, account=bank,
                                   debit=Decimal('1000'), credit=Decimal('0'))
        JournalLine.objects.create(journal=journal, line_no=20, account=revenue,
                                   debit=Decimal('0'), credit=Decimal('1000'))

    @staticmethod
    def _revenue(html):
        """Nilai KPI pertama (Revenue) - labelnya ikut bahasa aktif, jadi jangan
        di-match dari teks labelnya."""
        import re
        match = re.search(r'kpi-value[^"]*">([^<]+)<', html)
        return match.group(1).strip() if match else None

    def test_periode_oktober_memuat_revenue(self):
        html = self.client.get(
            reverse('reports_dashboard') + '?start=2026-10-01&end=2026-10-31').content.decode()
        self.assertIn('1.000', self._revenue(html) or '')
        # Tanggal tampil terlokalisasi (ID), bukan ISO.
        self.assertIn('Periode 1 Okt. 2026 s/d 31 Okt. 2026', html)

    def test_periode_lain_tidak_memuat_revenue(self):
        html = self.client.get(
            reverse('reports_dashboard') + '?start=2026-01-01&end=2026-01-31').content.decode()
        self.assertNotIn('1.000', self._revenue(html) or '')
        self.assertIn('Periode 1 Jan. 2026 s/d 31 Jan. 2026', html)




class JournalByCategoryTests(TestCase):
    """Jurnal umum dipisah per kategori internal perusahaan / pajak."""

    def setUp(self):
        group = Group.objects.create(name='Grup JC', code='GJC')
        self.company = Company.objects.create(
            company_code='JC', legal_name='PT JC', display_name='PT JC', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('jc', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.client.force_login(self.user)

        from gl.models import Account, AccountCategory, AccountingPeriod, FiscalYear

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Penjualan',
            account_category=AccountCategory.REVENUE, account_type='PRODUCT_REVENUE',
            normal_balance='C', is_postable=True)
        fy = FiscalYear.objects.create(
            company=self.company, name='2026', start_date='2026-01-01', end_date='2026-12-31')
        self.period = AccountingPeriod.objects.create(
            company=self.company, fiscal_year=fy, period_code='2026-10',
            period_name='Oktober 2026', start_date='2026-10-01', end_date='2026-10-31')

    def _journal(self, number, category):
        from gl.models import JournalEntry, JournalLine, JournalStatus

        journal = JournalEntry.objects.create(
            company=self.company, period=self.period, journal_number=number,
            journal_date='2026-10-10', source='MANUAL', category=category,
            status=JournalStatus.POSTED)
        JournalLine.objects.create(journal=journal, line_no=10, account=self.cash,
                                   debit=Decimal('100'), credit=Decimal('0'))
        JournalLine.objects.create(journal=journal, line_no=20, account=self.revenue,
                                   debit=Decimal('0'), credit=Decimal('100'))
        return journal

    def test_dipisah_per_kategori_dan_tampil_di_report(self):
        from gl.models import JournalCategory
        from reports import engine

        self._journal('INT-001', JournalCategory.INTERNAL)
        self._journal('TAX-001', JournalCategory.TAX)

        groups = engine.journal_by_category(Company.objects.filter(pk=self.company.pk))
        by_key = {g['key']: g for g in groups}
        self.assertEqual(len(by_key['INTERNAL']['rows']), 1)
        self.assertEqual(by_key['INTERNAL']['rows'][0]['journal'].journal_number, 'INT-001')
        self.assertEqual(by_key['TAX']['rows'][0]['journal'].journal_number, 'TAX-001')
        self.assertEqual(by_key['TAX']['total_debit'], Decimal('100'))

        html = self.client.get(reverse('reports_journal_by_category')).content.decode()
        self.assertIn('Internal Perusahaan', html)
        self.assertIn('Pajak', html)
        self.assertIn('INT-001', html)
        self.assertIn('TAX-001', html)


class BalanceComparisonTests(TestCase):
    """Neraca saldo perbandingan dua tanggal."""

    def setUp(self):
        group = Group.objects.create(name='Grup BC', code='GBC')
        self.company = Company.objects.create(
            company_code='BC', legal_name='PT BC', display_name='PT BC', group=group)
        Currency.objects.create(code='IDR', name='Rupiah', symbol='Rp')
        self.user = User.objects.create_user('bc', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.client.force_login(self.user)

        from gl.models import Account, AccountCategory, AccountingPeriod, FiscalYear

        self.cash = Account.objects.create(
            company=self.company, account_code='1101', account_name='Kas',
            account_category=AccountCategory.ASSET, account_type='CASH',
            normal_balance='D', is_postable=True)
        self.capital = Account.objects.create(
            company=self.company, account_code='3101', account_name='Modal',
            account_category=AccountCategory.EQUITY, account_type='PAID_IN_CAPITAL',
            normal_balance='C', is_postable=True)
        fy = FiscalYear.objects.create(
            company=self.company, name='2025', start_date='2025-01-01', end_date='2026-12-31')
        for code in ('2025-01', '2026-06'):
            AccountingPeriod.objects.create(
                company=self.company, fiscal_year=fy, period_code=code,
                period_name=code, start_date=f'{code}-01', end_date=f'{code}-28')

    def _post(self, date, lines):
        from gl.engine import JournalBuilder
        from gl.models import JournalSource
        builder = JournalBuilder(self.company, date, source=JournalSource.MANUAL)
        for account, side, amount in lines:
            getattr(builder, 'debit' if side == 'D' else 'credit')(account, Decimal(amount))
        return builder.save(auto_post=True)

    def test_engine_membandingkan_dua_tanggal(self):
        from reports import engine

        self._post('2025-01-10', [(self.cash, 'D', '1000'), (self.capital, 'C', '1000')])
        self._post('2026-06-10', [(self.cash, 'D', '500'), (self.capital, 'C', '500')])

        rows, totals = engine.balance_comparison(
            Company.objects.filter(pk=self.company.pk), '2025-12-31', '2026-12-31')
        by_code = {r['row']['account'].account_code: r for r in rows}
        self.assertEqual(by_code['1101']['from'], Decimal('1000'))
        self.assertEqual(by_code['1101']['to'], Decimal('1500'))
        self.assertEqual(by_code['1101']['delta'], Decimal('500'))
        self.assertEqual(by_code['3101']['from'], Decimal('-1000'))
        self.assertEqual(by_code['3101']['to'], Decimal('-1500'))
        self.assertEqual(totals['delta'], Decimal('0'), 'neraca harus seimbang')

    def test_halaman_render(self):
        self._post('2025-01-10', [(self.cash, 'D', '1000'), (self.capital, 'C', '1000')])
        html = self.client.get(
            reverse('reports_balance_comparison') + '?start=2025-12-31&end=2026-12-31').content.decode()
        self.assertIn('Neraca Banding', html)
        self.assertIn('Kas', html)
        self.assertIn('Modal', html)
        self.assertIn('Perubahan', html)
