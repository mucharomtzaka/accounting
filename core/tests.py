import re
from pathlib import Path

from django.contrib.auth.hashers import identify_hasher
from django.contrib.auth.models import Group as AuthGroup
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from .models import Company, Group, User, UserCompanyAccess


class UserAdminPasswordTests(TestCase):
    """Password lewat admin user HARUS jadi hash, bukan teks apa adanya.

    Regresi: UserAdmin dulu turun dari ModelAdmin, jadi field `password` di form
    tambah/ubah adalah CharField biasa. Nilai yang diketik tersimpan mentah dan
    `check_password()` selalu gagal - user tidak bisa login walau password sudah
    diisi. Test ini mengunci kontrak hash-nya.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('root', password='rootpw123')
        self.client.force_login(self.admin)

    @staticmethod
    def is_hashed(value):
        try:
            identify_hasher(value)
        except ValueError:
            return False
        return True

    def management_form(self, url):
        """Data management form inline (UserCompanyAccess/UserBranchAccess).

        Tanpa TOTAL_FORMS/INITIAL_FORMS, formset inline menganggap POST tidak
        lengkap dan seluruh form jadi tidak valid - browser selalu mengirimnya.
        """
        html = self.client.get(url).content.decode()
        data = {}
        for name in re.findall(r'name="([^"]+-(?:TOTAL|INITIAL|MIN_NUM|MAX_NUM)_FORMS)"', html):
            data[name] = '1000' if name.endswith('MAX_NUM_FORMS') else '0'
        self.assertTrue(any(n.endswith('-TOTAL_FORMS') for n in data), 'management form tidak ketemu')
        return data

    def test_tambah_user_lewat_admin_menghasilkan_hash(self):
        url = reverse('admin:core_user_add')
        data = self.management_form(url)
        data.update({
            'username': 'baru', 'password1': 'demo12345', 'password2': 'demo12345',
            'role': User.Role.ACCOUNTANT, 'is_staff': 'on',
        })
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username='baru')
        self.assertTrue(self.is_hashed(user.password))
        self.assertTrue(user.check_password('demo12345'))

    def test_ubah_user_tidak_menimpa_password_jadi_plaintext(self):
        user = User.objects.create_user('ap', password='demo12345')
        before = user.password
        url = reverse('admin:core_user_change', args=[user.pk])
        data = self.management_form(url)
        data.update({
            'username': 'ap', 'first_name': 'Diubah',
            'role': user.role, 'is_active': 'on', 'is_staff': 'on',
            # date_joined editable di fieldset Dates, jadi ikut wajib diisi.
            'date_joined_0': '2026-01-01', 'date_joined_1': '00:00:00',
        })
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertEqual(user.password, before)
        self.assertTrue(user.check_password('demo12345'))

    def test_form_ubah_tidak_menyediakan_field_password_editable(self):
        user = User.objects.create_user('ar', password='demo12345')
        html = self.client.get(
            reverse('admin:core_user_change', args=[user.pk])).content.decode()
        self.assertNotIn('name="password"', html)
        self.assertIn(reverse('admin:password_change'), html)


class AdminLoginTests(TestCase):
    """Halaman login admin menyediakan toggle mata untuk field password."""

    def test_login_punya_toggle_password(self):
        html = self.client.get(reverse('admin:login')).content.decode()
        self.assertIn('js/password-toggle.js', html)
        self.assertIn('data-password-toggle', html)
        self.assertIn('password-toggle-wrap', html)

    def test_panel_akun_demo_hanya_muncul_kalau_ada_user_demo(self):
        html = self.client.get(reverse('admin:login')).content.decode()
        # CSS-nya selalu ada, jadi yang dicek elemen panel-nya.
        self.assertNotIn('<details class="demo-accounts"', html,
                         'panel tidak boleh muncul tanpa user demo')

        User.objects.create_user('demo_a_owner', password='demo123', is_staff=True)
        html = self.client.get(reverse('admin:login')).content.decode()
        self.assertIn('<details class="demo-accounts"', html)
        self.assertIn('data-demo-user="demo_a_owner"', html)
        self.assertIn('demo-login.js', html)
        self.assertIn('demo123', html)


class AdminPopupFormTests(TestCase):
    """Form popup relasi admin (?_popup=1) dipusatkan oleh admin-nav.css.

    Django hanya melepas min-width di body.popup; tanpa aturan sendiri form
    tetap menempel kiri selebar jendela.
    """

    def setUp(self):
        self.client.force_login(User.objects.create_superuser('root2', password='rootpw123'))

    def test_popup_memuat_css_admin_nav(self):
        html = self.client.get('/admin/gl/account/add/?_popup=1').content.decode()
        self.assertIn('class="popup', html)
        self.assertIn('admin-nav.css', html)

    def test_css_memusatkan_form_popup(self):
        css = (Path(__file__).resolve().parent.parent / 'static' / 'css' / 'admin-nav.css').read_text()
        self.assertIn('body.popup #content', css)
        self.assertIn('body.popup .submit-row', css)


class MenuTranslationTests(TestCase):
    """Nama menu (app + model) harus tampil dalam bahasa Indonesia.

    verbose_name dulu string literal tanpa gettext, jadi locale/id tidak pernah
    kena. Test ini mengunci dua hal sekaligus: sumber sudah dibungkus gettext dan
    terjemahannya benar-benar ter-compile ke .mo.
    """

    def setUp(self):
        self.client.force_login(User.objects.create_superuser('root3', password='rootpw123'))

    def test_menu_admin_index_bahasa_indonesia(self):
        from django.utils import translation

        with translation.override('id'):
            html = self.client.get('/admin/').content.decode()
        for label in ['Buku Besar', 'Piutang Usaha', 'Utang Usaha', 'Kas &amp; Bank', 'Aset Tetap',
                      'Alur Persetujuan', 'Konsolidasi', 'Notifikasi', 'Core - Organisasi']:
            self.assertIn(label, html, f'menu {label!r} tidak terjemah')

    def test_menu_model_di_app_index_bahasa_indonesia(self):
        from django.utils import translation

        with translation.override('id'):
            html = self.client.get('/admin/gl/').content.decode()
        for label in ['Akun', 'Jurnal', 'Baris Jurnal', 'Periode Akuntansi', 'Tahun Buku',
                      'Template Jurnal', 'Pemetaan Akun']:
            self.assertIn(label, html, f'model {label!r} tidak terjemah')

    def test_nama_menu_tetap_inggris_di_lokale_en(self):
        from django.utils import translation
        from django.utils.translation import gettext as _

        with translation.override('en'):
            self.assertEqual(_('General Ledger'), 'General Ledger')
            self.assertEqual(_('Journal Entries'), 'Journal Entries')


class QuickMenuPermissionTests(TestCase):
    """Kartu quick menu di /admin/ hanya muncul sesuai izin modelnya."""

    def setUp(self):
        from django.contrib.auth.models import Permission

        group = Group.objects.create(name='Grup QM', code='GQM')
        self.company = Company.objects.create(
            company_code='QM', legal_name='PT QM', display_name='PT QM', group=group)
        self.Permission = Permission

    def _html_for(self, codenames):
        user = User.objects.create_user(
            f'u_{"_".join(codenames) or "none"}', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=user, company=self.company)
        user.user_permissions.add(*self.Permission.objects.filter(codename__in=codenames))
        self.client.force_login(user)
        return self.client.get('/admin/').content.decode()

    def test_hanya_izin_ar_muncul_kartu_aging(self):
        html = self._html_for(['view_customerinvoice'])
        self.assertIn(reverse('reports_aging'), html)
        self.assertIn(reverse('reports_dashboard'), html)
        self.assertNotIn(reverse('reports_trial_balance'), html)
        self.assertNotIn(reverse('admin:gl_account_changelist'), html)
        self.assertNotIn(reverse('admin:core_user_changelist'), html)

    def test_izin_jurnal_muncul_laporan_keuangan(self):
        html = self._html_for(['view_journalentry'])
        self.assertIn(reverse('reports_trial_balance'), html)
        self.assertIn(reverse('reports_general_ledger'), html)
        self.assertNotIn(reverse('reports_aging'), html)

    def test_izin_akun_muncul_master_coa(self):
        html = self._html_for(['view_account'])
        self.assertIn(reverse('admin:gl_account_changelist'), html)

    def test_izin_user_muncul_kartu_pengguna(self):
        html = self._html_for(['view_user'])
        self.assertIn(reverse('admin:core_user_changelist'), html)

    def test_tanpa_izin_hanya_dashboard(self):
        html = self._html_for([])
        self.assertIn(reverse('reports_dashboard'), html)
        self.assertNotIn(reverse('reports_trial_balance'), html)
        self.assertNotIn(reverse('admin:core_user_changelist'), html)


class RegisterPagePasswordToggleTests(TestCase):
    """Form register company menyediakan toggle mata untuk field password."""

    def test_register_punya_toggle_password(self):
        html = self.client.get(reverse('core_register_company')).content.decode()
        self.assertIn('js/password-toggle.js', html)
        self.assertIn('data-password-toggle', html)
        self.assertIn('password-toggle-wrap', html)
        self.assertEqual(html.count('type="password"'), 2, 'harus ada password1 & password2')


class OwnerPermissionCoverageTests(TestCase):
    """Owner/Finance harus dapat semua model company-scoped.

    Daftar permission per role itu manual, jadi gampang tertinggal saat ada
    model baru (dulu Warehouse & StockOpname kelewat). Test ini membandingkan
    dengan `_scoped_models()` yang diturunkan dari registry admin, jadi model
    company-scoped baru otomatis ketahuan kalau belum diberikan.
    """

    def test_owner_dan_finance_menutup_semua_model_scoped(self):
        from core.management.commands.seed_demo_users import (
            NEVER_GRANTED, ROLE_PERMISSIONS, _scoped_models,
        )

        scoped = _scoped_models() - NEVER_GRANTED
        for role in (User.Role.COMPANY_OWNER, User.Role.COMPANY_FINANCE):
            granted = {m for models, _ in ROLE_PERMISSIONS[role] for m in models}
            missing = sorted(scoped - granted)
            self.assertEqual(
                missing, [],
                f'{role} belum diberi permission untuk model company-scoped: {missing}',
            )


class RegisterOwnerPermissionsTests(TestCase):
    """Register company: owner langsung dapat izin seluruh fitur company-scoped."""

    def _register(self, code='RGX'):
        return self.client.post(reverse('core_register_company'), {
            'company_code': code, 'legal_name': f'PT {code}', 'display_name': f'PT {code}',
            'base_currency': 'IDR', 'npwp': '01.234.567.8-999.000',
            'username': f'owner_{code.lower()}', 'email': 'owner@contoh.co.id',
            'first_name': 'Owner', 'last_name': 'Baru',
            'password1': 'rahasia12345', 'password2': 'rahasia12345',
        })

    def test_owner_dapat_semua_izin_company_scoped(self):
        from core.management.commands.seed_demo_users import ROLE_PERMISSIONS, _scoped_models

        response = self._register()
        self.assertEqual(response.status_code, 302)

        user = User.objects.get(username='owner_rgx')
        self.assertEqual(user.role, User.Role.COMPANY_OWNER)
        self.assertFalse(user.is_superuser, 'owner tidak boleh superuser (Rule 1)')
        self.assertTrue(user.groups.filter(name='Company Owner - RGX').exists())

        for perm in ['gl.view_account', 'gl.view_journalentry', 'ar.add_customerinvoice',
                     'ap.add_supplierbill', 'inventory.view_warehouse',
                     'inventory.view_stockopname', 'core.view_branch', 'core.view_user']:
            self.assertTrue(user.has_perm(perm), f'owner kehilangan izin {perm}')

        # Tidak boleh ada model di luar daftar company-scoped yang diberikan.
        granted = {m for models, _ in ROLE_PERMISSIONS[User.Role.COMPANY_OWNER] for m in models}
        self.assertEqual(sorted(granted - _scoped_models()), [])
        self.assertFalse(user.has_perm('core.view_company'))

    def test_owner_bisa_buka_menu_inti_setelah_register(self):
        self._register()
        user = User.objects.get(username='owner_rgx')
        self.client.force_login(user)
        for url in ['/admin/', '/admin/gl/', '/admin/inventory/warehouse/',
                    '/admin/inventory/stockopname/', '/admin/core/branch/']:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class AdminActionPermissionTests(TestCase):
    """Action yang menulis data tidak muncul untuk user read-only (view saja)."""

    def _request_for(self, codenames):
        from django.test import RequestFactory

        user = User.objects.create_user(
            f'act_{"_".join(codenames) or "none"}', password='pw', is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(codename__in=codenames))
        request = RequestFactory().get('/admin/')
        request.user = user
        return request

    def test_action_jurnal_hanya_untuk_yang_bisa_change(self):
        from django.contrib.admin.sites import site
        from gl.models import JournalEntry

        admin_obj = site._registry[JournalEntry]
        view_only = admin_obj.get_actions(self._request_for(['view_journalentry']))
        for name in ['submit_journal', 'approve_journal', 'post_journal',
                     'reverse_journal', 'void_journal']:
            self.assertNotIn(name, view_only)

        with_change = admin_obj.get_actions(
            self._request_for(['view_journalentry', 'change_journalentry']))
        self.assertIn('post_journal', with_change)

    def test_action_export_tetap_ada_tanpa_change(self):
        from django.contrib.admin.sites import site
        from gl.models import Account

        admin_obj = site._registry[Account]
        actions = admin_obj.get_actions(self._request_for(['view_account']))
        self.assertIn('export_accounts_csv', actions, 'export hanya baca, tetap boleh')
        self.assertNotIn('deactivate', actions, 'deactivate mengubah data')

    def test_action_stock_opname_hanya_untuk_change(self):
        from django.contrib.admin.sites import site
        from inventory.models import StockOpname

        admin_obj = site._registry[StockOpname]
        view_only = admin_obj.get_actions(self._request_for(['view_stockopname']))
        self.assertNotIn('post_opname', view_only)
        self.assertNotIn('mark_counted', view_only)

    def test_tombol_dashboard_ikut_izin(self):
        group = Group.objects.create(name='Grup D', code='GD')
        company = Company.objects.create(
            company_code='D', legal_name='PT D', display_name='PT D', group=group)
        user = User.objects.create_user('viewer_d', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=user, company=company)
        user.user_permissions.add(*Permission.objects.filter(codename='view_customerinvoice'))

        self.client.force_login(user)
        html = self.client.get(reverse('reports_dashboard')).content.decode()
        self.assertIn(reverse('admin:ar_customerinvoice_changelist'), html)
        self.assertNotIn(reverse('admin:ap_supplierbill_changelist'), html)
        self.assertNotIn(reverse('admin:gl_journalentry_changelist'), html)


class AuthGroupCompanyScopeTests(TestCase):
    """Changelist auth.Group hanya menampilkan grup company aktif."""

    def setUp(self):
        self.group = Group.objects.create(name='Grup AG', code='GAG')
        self.c1 = Company.objects.create(
            company_code='P1', legal_name='PT P1', display_name='PT P1', group=self.group)
        self.c2 = Company.objects.create(
            company_code='P2', legal_name='PT P2', display_name='PT P2', group=self.group)
        AuthGroup.objects.create(name='Company Owner - P1')
        AuthGroup.objects.create(name='Company Owner - P2')
        AuthGroup.objects.create(name='Tanpa Company')
        self.client.force_login(User.objects.create_superuser('su_ag', password='pw'))

    def _names(self):
        import re
        html = self.client.get(reverse('admin:auth_group_changelist')).content.decode()
        return re.findall(r'field-name">\s*(?:<a[^>]*>)?([^<]+)', html)

    def test_hanya_satu_company_dan_grup_tanpa_company_disembunyikan(self):
        names = self._names()
        self.assertEqual(len(names), 1, names)
        self.assertNotIn('Tanpa Company', names)

    def test_ikut_company_aktif(self):
        self.client.get(reverse('core_switch_company'), {'company_id': self.c2.pk})
        self.assertEqual(self._names(), ['Company Owner - P2'])
        self.client.get(reverse('core_switch_company'), {'company_id': self.c1.pk})
        self.assertEqual(self._names(), ['Company Owner - P1'])


class CompanyScopedFilterTests(TestCase):
    """Filter sidebar FK company mengikuti company yang boleh diakses user."""

    def setUp(self):
        from django.test import RequestFactory

        self.rf = RequestFactory()
        group = Group.objects.create(name='Grup CF', code='GCF')
        self.a = Company.objects.create(
            company_code='CFA', legal_name='PT CFA', display_name='PT CFA', group=group)
        self.b = Company.objects.create(
            company_code='CFB', legal_name='PT CFB', display_name='PT CFB', group=group)

    def _choices(self, user, model, path='company'):
        from django.contrib.admin.sites import site

        admin_obj = site._registry[model]
        request = self.rf.get('/admin/')
        request.user = user
        request.session = {}
        entry = [f for f in admin_obj.get_list_filter(request)
                 if isinstance(f, tuple) and f[0] == path][0]
        # field awal path saja; CompanyScopedFilter tidak memakai field-nya,
        # hanya memilih dari allowed_companies.
        field = model._meta.get_field(path.split('__')[0])
        instance = entry[1](field, request, {}, model, admin_obj, path)
        return sorted(name for _, name in instance.lookup_choices)

    def test_user_company_scoped_hanya_lihat_companynya(self):
        from gl.models import JournalEntry

        user = User.objects.create_user('cf', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=user, company=self.a)

        choices = self._choices(user, JournalEntry)
        self.assertEqual(choices, ['CFA - PT CFA'])

    def test_superuser_lihat_semua(self):
        from gl.models import JournalEntry

        root = User.objects.create_superuser('cf_root', password='pw')
        choices = self._choices(root, JournalEntry)
        self.assertIn('CFA - PT CFA', choices)
        self.assertIn('CFB - PT CFB', choices)

    def test_filter_lewat_relasi_ikut_discope(self):
        from inventory.models import InventoryTransaction

        user = User.objects.create_user('cf2', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=user, company=self.b)

        choices = self._choices(user, InventoryTransaction, path='inventory__company')
        self.assertEqual(choices, ['CFB - PT CFB'])


class InheritedCompanyFilterScopeTests(CompanyScopedFilterTests):
    """Admin yang mewarisi base company-scoped (AP/AR) juga ikut ter-scope."""

    def test_admin_turunan_base_ikut_scoped(self):
        from ap.models import SupplierBill
        from ar.models import CustomerInvoice

        user = User.objects.create_user('inherited', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=user, company=self.a)

        self.assertEqual(self._choices(user, SupplierBill), ['CFA - PT CFA'])
        self.assertEqual(self._choices(user, CustomerInvoice), ['CFA - PT CFA'])


class ReportFilterAlignmentTests(TestCase):
    """Kontrol filter report disamakan tingginya supaya sejajar satu baris."""

    def test_css_menyamakan_tinggi_kontrol(self):
        css = (Path(__file__).resolve().parent.parent / 'static' / 'css' / 'dist' / 'styles.css').read_text()
        self.assertRegex(
            css, r'\.report-filter input,\.report-filter select\{[^}]*height:2\.5rem')
        self.assertRegex(css, r'\.report-filter label\{[^}]*white-space:nowrap')
        self.assertRegex(css, r'\.report-filter \.btn-apply\{[^}]*height:2\.5rem')


class CompanyScopedFormTests(TestCase):
    """Dropdown FK Company di form add/change mengikuti auth login."""

    def setUp(self):
        from django.test import RequestFactory

        self.rf = RequestFactory()
        group = Group.objects.create(name='Grup Form', code='GFM')
        self.a = Company.objects.create(
            company_code='FMA', legal_name='PT FMA', display_name='PT FMA', group=group)
        self.b = Company.objects.create(
            company_code='FMB', legal_name='PT FMB', display_name='PT FMB', group=group)

    def _company_choices(self, user, model):
        from django.contrib.admin.sites import site

        request = self.rf.get('/admin/')
        request.user = user
        request.session = {}
        form = site._registry[model].get_form(request)()
        return sorted(str(c) for c in form.fields['company'].queryset)

    def test_form_user_company_scoped(self):
        from gl.models import JournalTemplate

        user = User.objects.create_user('fmu', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=user, company=self.a)
        self.assertEqual(self._company_choices(user, JournalTemplate), ['FMA - PT FMA'])

    def test_form_superuser_semua_company(self):
        from gl.models import JournalTemplate

        root = User.objects.create_superuser('fm_root', password='pw')
        choices = self._company_choices(root, JournalTemplate)
        self.assertIn('FMA - PT FMA', choices)
        self.assertIn('FMB - PT FMB', choices)

    def test_changelist_journal_template_scoped(self):
        from gl.models import JournalTemplate

        JournalTemplate.objects.create(company=self.a, name='T-A')
        JournalTemplate.objects.create(company=self.b, name='T-B')
        user = User.objects.create_user('fmo', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=user, company=self.a)
        user.user_permissions.add(*Permission.objects.filter(
            codename__in=['view_journaltemplate']))
        self.client.force_login(user)
        html = self.client.get('/admin/gl/journaltemplate/').content.decode()
        self.assertIn('T-A', html)
        self.assertNotIn('T-B', html)
