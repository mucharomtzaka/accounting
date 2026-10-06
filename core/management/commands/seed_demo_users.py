"""Seed demo user + role group untuk satu company.

Kenapa command terpisah dari seed_data: seed_data sudah jadi seed master data
(single source untuk company/COA/journal), sedangkan demo user perlu sering
di-refresh tanpa menjalankan ulang seluruh seed. Command ini idempotent.

CATATAN PENTING SOAL ROLE vs PERMISSION
--------------------------------------
`User.role` itu cuma CharField dekoratif - tidak ada satupun kode yang
membandingkan `user.role` untuk granting/denying akses. Yang benar-benar
mengatur akses adalah `auth.Group` + `auth.Permission` (Django ModelBackend).
Command ini karena itu tetap mengisi `role` (berguna untuk display & filter di
UserAdmin) TAPI sumber utama akses ada di permission group.

MEMBATASAN PERMISSION (Rule 1 company isolation)
------------------------------------------------
Permission hanya boleh diberikan untuk model yang ModelAdmin-nya sudah
company-scoped (`get_queryset` memfilter `allowed_companies`). Model yang tidak
scoped akan membocorkan data company lain ke user company-scoped.

Dahulu daftar ini diambil dari `core.views.SCOPED_MODELS`, tapi daftar tersebut
terbukti tidak sinkron dengan kode: 5 entri di sana ModelAdmin-nya tidak
scoped (ar.CustomerInvoiceLine, ap.SupplierBillLine, gl.Account, gl.JournalLine,
gl.AccountMapping) sementara 4 model yang scoped justru tidak terdaftar
(ar.CustomerInvoice, ar.CustomerPayment, ap.SupplierBill, ap.SupplierPayment).
`_scoped_models()` di bawah menurunkannya dari registry admin saat runtime
supaya yang diberikan benar-benar aman.
"""

from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import identify_hasher
from django.contrib.auth.models import Group as AuthGroup, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Company, UserCompanyAccess

User = get_user_model()

VIEW, ADD, CHANGE, DELETE = 'view', 'add', 'change', 'delete'
# Dipakai juga oleh template tag demo_login_accounts (halaman login) supaya
# password yang ditampilkan sama dengan yang di-seed.
DEFAULT_PASSWORD = 'demo123'
CRUD = (VIEW, ADD, CHANGE, DELETE)
READ = (VIEW,)

# Kelompok model per domain - dipisah supaya mapping role di bawah mudah dibaca.
AR = ('ar.Customer', 'ar.CustomerInvoice', 'ar.CustomerPayment', 'ar.CustomerInvoiceLine')
AP = ('ap.Supplier', 'ap.SupplierBill', 'ap.SupplierPayment', 'ap.SupplierBillLine')
# gl.Account ikut GL: kartu "Master COA" di quick menu admin index hanya muncul
# untuk user yang punya view_account, jadi tanpa ini semua user demo kehilangan
# kartu itu. Aman karena AccountAdmin.get_queryset sudah company-scoped
# (allowed_companies), dan `_scoped_models()` membuktikannya.
# AccountingPeriod + FiscalYear ikut GL: proses tutup buku bulanan/tahunan ada
# di dua model itu (lihat gl/services.py close_period / close_fiscal_year).
GL = ('gl.JournalEntry', 'gl.Account', 'gl.AccountingPeriod', 'gl.FiscalYear',
      'gl.AccountMapping', 'gl.JournalTemplate', 'gl.JournalLine')
BANK = ('bank.BankAccount', 'bank.BankTransaction', 'bank.BankReconciliation')
FA = ('fa.AssetCategory', 'fa.FixedAsset', 'fa.AssetDepreciation', 'fa.AssetTransaction')
INV = ('inventory.Inventory', 'inventory.InventoryTransaction',
       'inventory.Warehouse', 'inventory.StockOpname', 'inventory.InventoryCost')
PRODUCT = ('core.Product',)
# Master organisasi yang admin-nya sudah company-scoped: branch/department/
# cost center dipakai sebagai dimensi jurnal, kurs dipakai sebagai fallback FX.
ORG = ('core.Branch', 'core.Department', 'core.CostCenter', 'core.ExchangeRate')
BUDGET = ('core.Budget',)
TAX = ('core.TaxCode',)
PROJECT = ('core.Project', 'core.ProjectBilling', 'core.ProjectCost', 'core.ProjectMilestone')
WORKFLOW = ('workflow.ApprovalWorkflow', 'workflow.ApprovalRequest', 'core.ApprovalLimit')
CONSOL = ('consolidation.IntercompanyTransaction', 'consolidation.ConsolidationRun')
NOTIFY = ('notify.Notification',)
# Eksekusi pembayaran dipisah dari pembuatan dokumen: treasury yang menjalankan
# transfer bank, AR/AP officer yang membuat invoice/bill. Ini inti segregation of
# duties di proses finance - satu orang tidak boleh membuat tagihan sekaligus
# membayarnya.
PAYMENTS = ('ar.CustomerPayment', 'ap.SupplierPayment')
# Model company-scoped yang sengaja TIDAK pernah diberikan ke role demo:
# mengelola grup izin sendiri = bisa menaikkan hak akses (privilege escalation).
NEVER_GRANTED = {'auth.Group', 'core.AuditLog'}

# core.User ikut karena kartu "User" / "Tambah User" di quick menu admin index
# membutuhkannya, dan UserAdmin sudah company-scoped (lihat get_queryset-nya).
# Tanpa DELETE: user yang bisa menghapus user bisa mencabut akses orang lain.
USER = ('core.User',)
USER_MANAGE = (VIEW, ADD, CHANGE)
# AuditLog di admin sudah read-only (add/change/delete dimatikan), tapi role
# owner/finance tetap perlu permission `view` supaya fitur audit bisa dibuka.
AUDIT_LOG = ('core.AuditLog',)

ALL_MODELS = (AR + AP + GL + BANK + FA + INV + PRODUCT + ORG + BUDGET + TAX + PROJECT
              + WORKFLOW + CONSOL + NOTIFY)

# Role yang didemokan: level company saja. SUPER_ADMIN & GROUP_FINANCE dilewati
# karena sifatnya lintas-company - demo user di-scope ke satu company, jadi
# memberi role itu hanya akan membingungkan (is_group_level sendiri tak terpakai).
DEMO_ROLES = (
    User.Role.COMPANY_OWNER,
    User.Role.COMPANY_FINANCE,
    User.Role.ACCOUNTANT,
    User.Role.AR_OFFICER,
    User.Role.AP_OFFICER,
    User.Role.TREASURY,
    User.Role.TAX_OFFICER,
    User.Role.BUDGET_CONTROLLER,
    User.Role.PROJECT_FINANCE,
    User.Role.AUDITOR,
    User.Role.MANAGEMENT,
)

# username slug + nama demonstratif.
USER_PROFILE = {
    User.Role.COMPANY_OWNER: ('owner', 'Budi Santoso'),
    User.Role.COMPANY_FINANCE: ('finance', 'Siti Rahayu'),
    User.Role.ACCOUNTANT: ('accountant', 'Andi Wijaya'),
    User.Role.AR_OFFICER: ('ar', 'Dewi Lestari'),
    User.Role.AP_OFFICER: ('ap', 'Rudi Hartono'),
    User.Role.TREASURY: ('treasury', 'Maya Puspita'),
    User.Role.TAX_OFFICER: ('tax', 'Hendra Gunawan'),
    User.Role.BUDGET_CONTROLLER: ('budget', 'Ratna Sari'),
    User.Role.PROJECT_FINANCE: ('project', 'Doni Pranoto'),
    User.Role.AUDITOR: ('auditor', 'Fitri Handayani'),
    User.Role.MANAGEMENT: ('management', 'Agus Salim'),
}

# Mapping role -> [(models, actions)]. Least privilege: pemisahan tugas antara
# AR / AP / Treasury, dan Auditor + Management read-only.
ROLE_PERMISSIONS = {
    User.Role.COMPANY_OWNER: [(ALL_MODELS, CRUD), (USER, USER_MANAGE), (AUDIT_LOG, READ)],
    User.Role.COMPANY_FINANCE: [(ALL_MODELS, CRUD), (USER, USER_MANAGE)],
    User.Role.ACCOUNTANT: [
        (AR + AP + GL + BANK + FA + INV + PRODUCT + NOTIFY, CRUD),
        (WORKFLOW, (VIEW, ADD, CHANGE)),
        (BUDGET + TAX + PROJECT + CONSOL + ORG, READ),
    ],
    User.Role.AR_OFFICER: [
        (AR, CRUD), (GL, READ), (AP, READ), (PRODUCT, READ), (WORKFLOW, READ), (ORG, READ),
    ],
    User.Role.AP_OFFICER: [
        (AP, CRUD), (GL, READ), (AR, READ), (PRODUCT, READ), (WORKFLOW, READ), (ORG, READ),
    ],
    # Treasury: bank + eksekusi/koreksi pembayaran (masuk & keluar). AR/AP
    # officer tetap boleh mencatat penerimaan/pembayaran atas dokumennya, tapi
    # rekening bank dan transfer antar bank hanya treasury.
    User.Role.TREASURY: [
        (BANK, CRUD), (PAYMENTS, CRUD), (GL, READ), (AP, READ), (AR, READ), (ORG, READ),
    ],
    User.Role.TAX_OFFICER: [
        (TAX, CRUD), (GL, READ), (AR, READ), (AP, READ), (PRODUCT, READ), (ORG, READ),
    ],
    User.Role.BUDGET_CONTROLLER: [
        (BUDGET, CRUD), (GL, READ), (AR, READ), (AP, READ), (PROJECT, READ), (ORG, READ),
    ],
    User.Role.PROJECT_FINANCE: [
        (PROJECT, CRUD), (GL, READ), (AR, READ), (AP, READ), (INV, READ), (ORG, READ),
    ],
    User.Role.AUDITOR: [(ALL_MODELS, READ), (USER, READ)],
    User.Role.MANAGEMENT: [(ALL_MODELS, READ), (USER, READ)],
}


def _scoped_models():
    """Model yang ModelAdmin-nya company-scoped, diturunkan dari registry admin.

    Guard-nya `allowed_companies` di get_queryset - sama persis filter yang
    dipakai ~25 ModelAdmin. Model tanpa admin terdaftar tidak bisa di-scope,
    jadi otomatis tidak masuk daftar.
    """
    import inspect

    result = set()
    for model in apps.get_models():
        model_admin = admin.site._registry.get(model)
        if model_admin is None:
            continue
        for klass in type(model_admin).__mro__:
            if 'get_queryset' in klass.__dict__:
                if 'allowed_companies' in inspect.getsource(klass.__dict__['get_queryset']):
                    result.add(model._meta.label)
                break
    return result


def _password_is_hashed(value):
    """True kalau kolom password berisi hash yang bisa dicek `check_password`."""
    try:
        identify_hasher(value)
    except ValueError:
        return False
    return True


def sync_role_group(company, role):
    """Buat/sinkron group permission untuk (company, role) lalu kembalikan group.

    Dipakai `Command._sync_group` (seed demo) DAN views.register_company, supaya
    owner hasil register dapat izin yang sama persis dengan owner demo - bukan
    daftar model terpisah yang bisa basi. Fail cepat kalau ada model di mapping
    yang belum company-scoped (memberikannya = bocor antar company).
    """
    label = dict(User.Role.choices)[role]
    group, _ = AuthGroup.objects.get_or_create(name=f'{label} - {company.company_code}')

    scoped = _scoped_models()
    wanted_labels = {m for models, _ in ROLE_PERMISSIONS[role] for m in models}
    unsafe = sorted(wanted_labels - scoped)
    if unsafe:
        raise CommandError(
            'Model berikut tidak company-scoped, tidak boleh diberi permission: '
            + ', '.join(unsafe)
        )

    # Resolusi lewat model class, bukan string: ContentType.model disimpan huruf
    # kecil ('customerinvoice') sehingga pencocokan string case-sensitive gagal,
    # dan filter app_label x model menghasilkan cross-product false positive.
    # Codename dibangun eksplisit lalu dicari dengan __in, bukan startswith:
    # startswith dengan tuple prefix di MySQL mengembalikan 0 baris.
    wanted = []
    for models, actions in ROLE_PERMISSIONS[role]:
        for model_label in models:
            app_label, model_name = model_label.split('.')
            try:
                model = apps.get_model(app_label, model_name)
            except LookupError as exc:
                raise CommandError(f'Model {model_label} tidak dikenal: {exc}')
            content_type = ContentType.objects.get_for_model(model)
            codenames = [f'{action}_{model._meta.model_name}' for action in actions]
            wanted.extend(Permission.objects.filter(
                content_type=content_type, codename__in=codenames,
            ))

    group.permissions.set(wanted)
    return group


class Command(BaseCommand):
    help = 'Buat demo user + permission group untuk satu company (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('--company', default='A', help='company_code tujuan; default A')
        parser.add_argument('--password', default=DEFAULT_PASSWORD, help='password semua demo user')
        parser.add_argument(
            '--roles', nargs='*', choices=[r.value for r in DEMO_ROLES], default=None,
            help='Batasi ke role tertentu; default semua role level-company',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        code = options['company']
        try:
            company = Company.objects.get(company_code=code)
        except Company.DoesNotExist:
            available = ', '.join(Company.objects.values_list('company_code', flat=True))
            raise CommandError(f'Company {code!r} tidak ada. Tersedia: {available}')

        roles = [r for r in DEMO_ROLES if options['roles'] is None or r.value in options['roles']]
        if not roles:
            raise CommandError('Tidak ada role yang cocok dengan --roles.')

        scoped = _scoped_models()
        wanted = {m for r in roles for models, _ in ROLE_PERMISSIONS[r] for m in models}
        unsafe = sorted(wanted - scoped)
        if unsafe:
            # Fail cepat: jangan sampai permission bocor ke company lain diam-diam.
            raise CommandError(
                'Model berikut tidak company-scoped, aman untuk diberikan permission: '
                + ', '.join(unsafe)
            )

        self.stdout.write(f'Company   : {company}')
        self.stdout.write(f'Password  : {options["password"]} (semua demo user)')
        self.stdout.write(f'Role      : {len(roles)} user\n')

        rows = []
        for role in roles:
            user, perms = self._sync_user(company, role, options['password'])
            UserCompanyAccess.objects.get_or_create(
                user=user, company=company, defaults={'is_active': True},
            )
            rows.append((user.username, user.get_full_name(), user.get_role_display(), perms))

        width = max(len(r[0]) for r in rows)
        for username, fullname, role_label, n_perms in rows:
            self.stdout.write(f'  {username:<{width}}  {fullname:<17} {role_label:<18} {n_perms:>3} perm')
        self.stdout.write(self.style.SUCCESS(f'\n{len(rows)} demo user siap di company {code}.'))

    def _sync_user(self, company, role, password):
        """Buat/update user, lalu set permission group persis sesuai mapping role."""
        slug, fullname = USER_PROFILE[role]
        first, _, last = fullname.partition(' ')
        username = f'demo_{company.company_code.lower()}_{slug}'

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'first_name': first, 'last_name': last,
                'email': f'{username}@contoh.co.id',
                'role': role, 'is_staff': True, 'is_superuser': False,
            },
        )
        changed = False
        # Samakan ulang field yang bisa berubah, tapi JANGAN sentuh password user
        # yang sudah ada - demo command harus bisa di-refresh tanpa mereset kredensial.
        for field, value in (
            ('first_name', first), ('last_name', last),
            ('role', role), ('is_staff', True), ('is_superuser', False),
        ):
            if getattr(user, field) != value:
                setattr(user, field, value)
                changed = True
        if changed:
            user.save()
        if created or not _password_is_hashed(user.password):
            # Password plaintext (mis. hasil edit manual lewat SQL) tidak pernah
            # bisa login: check_password() menolak string yang bukan hash. Jadi
            # di sini dikembalikan ke hash, command ini memang utility demo.
            user.set_password(password)
            user.save(update_fields=['password'])

        group = self._sync_group(company, role)
        user.groups.set([group])
        return user, group.permissions.count()

    def _sync_group(self, company, role):
        """Wrapper tipis: sumber logika ada di sync_role_group()."""
        return sync_role_group(company, role)