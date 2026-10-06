"""Seed data awal: superuser, group, company, COA, fiscal year, period, currency."""
from datetime import date, timedelta

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.coa_template import seed_coa
from core.models import (
    Branch, Company, Currency, Department, CostCenter, Group, TaxCode, User, UserCompanyAccess,
)
from gl.models import AccountingPeriod, FiscalYear
from gl.services import generate_periods

CURRENCIES = [
    ('IDR', 'Indonesian Rupiah', 'Rp'),
    ('USD', 'US Dollar', '$'),
    ('SGD', 'Singapore Dollar', 'S$'),
    ('EUR', 'Euro', 'EUR'),
    ('MYR', 'Malaysian Ringgit', 'RM'),
]


class Command(BaseCommand):
    help = 'Seed data awal: currency, superuser, group, company, COA, fiscal year, period'

    def add_arguments(self, parser):
        parser.add_argument('--username', default='admin')
        parser.add_argument('--password', default='admin123')
        parser.add_argument('--superuser-email', default='admin@accounting.local')
        parser.add_argument('--demo', action='store_true',
                            help='Generate demo data: customers, suppliers, bank, invoices, bills, payments, asset, project, trend')
        parser.add_argument('--demo-password', default='demo123',
                            help='Password user demo (dipakai oleh seed_demo_users)')

    @transaction.atomic
    def handle(self, *args, **options):
        for code, name, symbol in CURRENCIES:
            Currency.objects.get_or_create(code=code, defaults={'name': name, 'symbol': symbol})

        user = self._superuser(options)
        group = self._group()
        companies = self._companies(group)
        for company in companies:
            accounts = seed_coa(company)
            self._tax_codes(company, accounts)
            self._organization(company)
            self._fiscal_year(company)

        for company in companies:
            UserCompanyAccess.objects.get_or_create(user=user, company=company)

        if options['demo']:
            for company in companies:
                self._demo_data(company)
            # Role + user demo ikut di-seed supaya contoh langsung punya
            # pembagian tugas accounting/finance (AR/AP/Treasury/Tax/...).
            call_command('seed_demo_users', company=companies[0].company_code,
                         password=options['demo_password'])
            # Request persetujuan butuh user demo (requester/approver), jadi
            # dibuat setelah seed_demo_users.
            for company in companies:
                self._demo_approval_requests(company)

        self.stdout.write(self.style.SUCCESS('\nSeed selesai.'))
        self.stdout.write(f'  Superuser : {user.username} / {options["password"]}')
        self.stdout.write(f'  Company   : {", ".join(c.company_code for c in companies)}')
        self.stdout.write('  Reports   : /admin/reports/')
        self.stdout.write('  COA       : /admin/gl/account/')
        if options['demo']:
            self.stdout.write('  Demo data: customers, suppliers, bank, invoices, bills, payments,\n'
                              '             asset, project, persediaan, alur persetujuan (workflow+request),\n'
                              '             tren 12 bulan, user+role demo')

    def _demo_approval_workflows(self, company):
        """PRD 30 - contoh approval workflow + step berdasarkan limit nominal.

        Batas atas inklusif (0-10jt, 10jt-100jt, >100jt): nominal tepat di batas
        terkena step yang lebih rendah karena `build_request` memilih step
        pertama yang cocok. Sesuaikan angka kalau mau kebijakan sebaliknya.
        """
        from workflow.models import ApprovalStep, ApprovalWorkflow

        specs = [
            ('Persetujuan Tagihan Pemasok', 'SupplierBill', [
                (10, User.Role.ACCOUNTANT, 0, 10_000_000),
                (20, User.Role.COMPANY_FINANCE, 10_000_000, 100_000_000),
                (30, User.Role.COMPANY_OWNER, 100_000_000, None),
            ]),
            ('Persetujuan Penerimaan Pelanggan', 'CustomerPayment', [
                (10, User.Role.AR_OFFICER, 0, 50_000_000),
                (20, User.Role.COMPANY_FINANCE, 50_000_000, None),
            ]),
        ]
        for name, model_name, steps in specs:
            workflow, _ = ApprovalWorkflow.objects.get_or_create(
                company=company, name=name, defaults={'model_name': model_name})
            for order, role, low, high in steps:
                ApprovalStep.objects.get_or_create(
                    workflow=workflow, step_order=order,
                    defaults={'required_role': role, 'min_amount': low, 'max_amount': high},
                )

    def _demo_approval_requests(self, company):
        """Contoh request: satu masih PENDING, satu sudah APPROVED."""
        from core.models import Currency
        from workflow.models import ApprovalRequest, ApprovalWorkflow, WorkflowStatus

        workflow = ApprovalWorkflow.objects.filter(
            company=company, model_name='SupplierBill').first()
        if workflow is None:
            return

        prefix = f'demo_{company.company_code.lower()}_'
        users = {u.username: u for u in User.objects.filter(username__startswith=prefix)}
        requester = users.get(f'{prefix}accountant') or users.get(f'{prefix}finance')
        if requester is None:
            return

        idr = Currency.objects.get(code='IDR')
        step_low = workflow.steps.filter(step_order=10).first()
        step_mid = workflow.steps.filter(step_order=20).first()
        samples = [
            ('BILL-DEMO-001', 'BILL-DEMO-001 - Supplier Demo', 30_000_000, step_mid,
             WorkflowStatus.PENDING, users.get(f'{prefix}finance'), None),
            ('BILL-TREND-001', 'BILL-TREND-001 - beban bulanan', 5_000_000, step_low,
             WorkflowStatus.APPROVED, users.get(f'{prefix}owner'), timezone.now()),
        ]
        for object_id, object_repr, amount, step, status, approver, action_at in samples:
            if step is None:
                continue
            ApprovalRequest.objects.get_or_create(
                workflow=workflow, object_id=object_id,
                defaults={
                    'step': step, 'requester': requester, 'approver': approver,
                    'model_name': workflow.model_name, 'object_repr': object_repr,
                    'amount': amount, 'currency': idr, 'status': status,
                    'action_at': action_at,
                },
            )

    def _demo_inventory(self, company, acc, revenue, expense, today):
        """Sample akuntansi persediaan: product, gudang, GR/GI, satu opname.

        Idempotent lewat cek reference GR-DEMO-001 (bukan cek count) supaya
        jalan ulang tidak menambah stok lagi.
        """
        from core.models import Product
        from inventory.models import (
            Inventory, InventoryTransaction, StockOpname, StockOpnameLine, Warehouse,
        )
        from inventory.services import goods_issue, goods_receipt, refresh_system_quantity

        inventory_asset = acc.get('1301')
        cogs_account = acc.get('5101') or expense

        specs = [
            {'code': 'PRD-DEMO1', 'name': 'Produk Demo 1', 'price': 250_000, 'cost': 150_000,
             'qty_in': 100, 'qty_out': 25},
            {'code': 'PRD-DEMO2', 'name': 'Produk Demo 2', 'price': 120_000, 'cost': 80_000,
             'qty_in': 60, 'qty_out': 10},
        ]
        products = []
        for spec in specs:
            product, _ = Product.objects.get_or_create(
                company=company, product_code=spec['code'],
                defaults={'product_name': spec['name'], 'uom': 'PCS',
                          'price': spec['price'], 'cost': spec['cost'],
                          'revenue_account': revenue, 'cogs_account': cogs_account,
                          'purchase_account': inventory_asset or cogs_account},
            )
            products.append(product)

        already = InventoryTransaction.objects.filter(
            product__product_code__startswith='PRD-DEMO', reference='GR-DEMO-001').exists()
        if not already:
            for product, spec in zip(products, specs):
                goods_receipt(product, spec['qty_in'], spec['cost'], reference='GR-DEMO-001',
                              description=f"Pembelian awal {product.product_code}",
                              transaction_date=today)
            for product, spec in zip(products, specs):
                goods_issue(product, spec['qty_out'], reference='GI-DEMO-001',
                            description=f"Penjualan {product.product_code}",
                            transaction_date=today)

        warehouse = Warehouse.objects.filter(company=company).first()
        if warehouse and not StockOpname.objects.filter(company=company).exists():
            opname = StockOpname.objects.create(
                company=company, warehouse=warehouse, opname_number='SO-DEMO-001',
                opname_date=today, status=StockOpname.Status.COUNTED,
                notes='Contoh opname: satu produk selisih -2, siap diposting.',
            )
            inventories = {inv.product_id: inv for inv in Inventory.objects.filter(company=company)}
            for index, product in enumerate(products):
                inventory = inventories.get(product.pk)
                if inventory is None:
                    continue
                counted = inventory.quantity_on_hand - 2 if index == 0 else inventory.quantity_on_hand
                StockOpnameLine.objects.create(
                    opname=opname, inventory=inventory, counted_quantity=counted)
            refresh_system_quantity(opname)

    def _demo_data(self, company):
        """Demo transaksi supaya report punya angka: AR, AP, bank, project, IC."""
        from ap.models import Supplier, SupplierBill, SupplierBillLine, SupplierPayment
        from ap.services import (
            allocate_payment as ap_allocate, post_bill, post_payment as ap_post_payment,
            recalc_bill_totals,
        )
        from ar.models import Customer, CustomerInvoice, CustomerInvoiceLine, CustomerPayment, InvoiceType
        from ar.services import (
            allocate_payment, post_invoice, post_payment, recalc_invoice_totals,
        )
        from bank.models import BankAccount, BankTransaction
        from bank.services import post_bank_transaction
        from consolidation.models import IntercompanyTransaction
        from core.models import Branch, Currency, ExchangeRate
        from core.project_models import Project, ProjectCost
        from fa.models import AssetCategory, FixedAsset
        from gl.models import Account

        idr = Currency.objects.get(code='IDR')
        usd = Currency.objects.get(code='USD')
        today = timezone.localdate()

        acc = {a.account_code: a for a in Account.objects.filter(company=company)}
        branch = Branch.objects.filter(company=company).first()

        revenue = acc.get('4101') or acc.get('4201')
        expense = acc.get('6101')
        if not revenue or not expense:
            self.stdout.write(f'  Demo {company.company_code}: dilewati (COA belum lengkap).')
            return

        cust_idr, _ = Customer.objects.get_or_create(
            company=company, customer_code='CUST-DEMO1',
            defaults={'customer_name': 'PT Demo Koperasi', 'currency': idr,
                      'payment_term_days': 30, 'credit_limit': 50_000_000},
        )
        cust_usd, _ = Customer.objects.get_or_create(
            company=company, customer_code='CUST-DEMO2',
            defaults={'customer_name': 'PT Demo Jaya', 'currency': usd, 'payment_term_days': 30},
        )
        supplier, _ = Supplier.objects.get_or_create(
            company=company, supplier_code='SUP-DEMO1',
            defaults={'supplier_name': 'Supplier Demo', 'currency': idr, 'payment_term_days': 30},
        )
        bank_acc, _ = BankAccount.objects.get_or_create(
            company=company, bank_name='Bank Demo', account_number='123-456-789',
            defaults={'account_name': 'Kas Demo', 'currency': idr,
                      'gl_account': acc.get('1101') or acc.get('1102'), 'is_default': True},
        )

        inv, _ = CustomerInvoice.objects.get_or_create(
            company=company, invoice_number='INV-DEMO-001',
            defaults={'invoice_date': today, 'due_date': today + timedelta(days=30),
                      'customer': cust_idr, 'branch': branch, 'currency': idr,
                      'invoice_type': InvoiceType.INVOICE},
        )
        CustomerInvoiceLine.objects.get_or_create(
            invoice=inv, line_no=1,
            defaults={'description': 'Produk A', 'quantity': 100, 'unit_price': 200_000,
                      'account': revenue},
        )

        inv_usd, _ = CustomerInvoice.objects.get_or_create(
            company=company, invoice_number='INV-DEMO-002',
            defaults={'invoice_date': today - timedelta(days=5),
                      'due_date': today + timedelta(days=25), 'customer': cust_usd, 'branch': branch,
                      'currency': usd, 'exchange_rate': 15_000, 'invoice_type': InvoiceType.INVOICE},
        )
        CustomerInvoiceLine.objects.get_or_create(
            invoice=inv_usd, line_no=1,
            defaults={'description': 'Jasa konsultansi', 'quantity': 1, 'unit_price': 1_000,
                      'account': acc.get('4201') or revenue},
        )

        bill, _ = SupplierBill.objects.get_or_create(
            company=company, bill_number='BILL-DEMO-001',
            defaults={'bill_date': today - timedelta(days=5), 'due_date': today + timedelta(days=25),
                      'supplier': supplier, 'branch': branch, 'currency': idr},
        )
        SupplierBillLine.objects.get_or_create(
            bill=bill, line_no=1,
            defaults={'description': 'Barang X', 'quantity': 10, 'unit_price': 3_000_000,
                      'account': expense},
        )

        recalc_invoice_totals(inv)
        recalc_invoice_totals(inv_usd)
        recalc_bill_totals(bill)

        posted = []
        for doc, fn, number in ((inv, post_invoice, 'INV-DEMO-001'),
                                (inv_usd, post_invoice, 'INV-DEMO-002'),
                                (bill, post_bill, 'BILL-DEMO-001')):
            doc.refresh_from_db()
            if doc.status != 'POSTED':
                try:
                    fn(doc)
                    posted.append(number)
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f'  Demo {company.company_code}: {exc}'))

        inv.refresh_from_db()
        cust_pay, _ = CustomerPayment.objects.get_or_create(
            company=company, payment_number='PAY-DEMO-001',
            defaults={'payment_date': today, 'customer': cust_idr, 'branch': branch,
                      'bank_account': bank_acc, 'currency': idr, 'amount': inv.total / 2},
        )
        if cust_pay.status == 'DRAFT':
            try:
                allocate_payment(cust_pay, inv, inv.total / 2)
                post_payment(cust_pay)
                posted.append(cust_pay.payment_number)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'  Demo {company.company_code}: payment AR {exc}'))

        bill.refresh_from_db()
        sup_pay, _ = SupplierPayment.objects.get_or_create(
            company=company, payment_number='PAY-SUP-DEMO-001',
            defaults={'payment_date': today, 'supplier': supplier, 'branch': branch,
                      'bank_account': bank_acc, 'currency': idr, 'amount': bill.total / 2},
        )
        if sup_pay.status == 'DRAFT':
            try:
                ap_allocate(sup_pay, bill, bill.total / 2)
                ap_post_payment(sup_pay)
                posted.append(sup_pay.payment_number)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'  Demo {company.company_code}: payment AP {exc}'))

        interest, _ = BankTransaction.objects.get_or_create(
            company=company, transaction_number='BANK-DEMO-001',
            defaults={'transaction_date': today, 'bank_account': bank_acc, 'branch': branch,
                      'tx_type': 'INTEREST', 'description': 'Bunga deposito',
                      'amount': 250_000, 'currency': idr},
        )
        if interest.status == 'DRAFT':
            try:
                post_bank_transaction(interest)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f'  Demo {company.company_code}: bunga {exc}'))

        category, _ = AssetCategory.objects.get_or_create(
            company=company, category_code='MESHIN',
            defaults={'category_name': 'Mesin Produksi', 'useful_life_years': 5,
                      'asset_account': acc.get('1401'),
                      'accumulated_account': acc.get('1402'),
                      'depreciation_account': acc.get('6401'),
                      'disposal_gain_account': acc.get('4310'),
                      'disposal_loss_account': acc.get('6910')},
        )
        FixedAsset.objects.get_or_create(
            company=company, asset_code='FA-DEMO-001',
            defaults={'asset_name': 'Mesin Produksi 01', 'asset_category': category,
                      'branch': branch, 'acquisition_date': today - timedelta(days=365),
                      'acquisition_cost': 100_000_000, 'residual_value': 10_000_000,
                      'useful_life_years': 5, 'status': 'ACTIVE',
                      'accumulated_depreciation': 0, 'net_book_value': 100_000_000},
        )

        project, _ = Project.objects.get_or_create(
            company=company, project_code='PRJ-DEMO-001',
            defaults={'project_name': 'Proyek Demo', 'project_type': 'SERVICE', 'branch': branch,
                      'customer': cust_idr, 'contract_value': 50_000_000,
                      'budget_cost': 40_000_000, 'status': 'IN_PROGRESS',
                      'wip_account': acc.get('1302')},
        )
        ProjectCost.objects.get_or_create(
            project=project, cost_date=today,
            defaults={'cost_type': 'MATERIAL', 'description': 'Material proyek',
                      'quantity': 1, 'unit_price': 10_000_000},
        )

        self._demo_inventory(company, acc, revenue, expense, today)
        self._demo_approval_workflows(company)

        ExchangeRate.objects.get_or_create(
            currency=usd, company=company, rate_date=today, defaults={'rate': 15_000},
        )

        peers = list(company.group.companies.exclude(pk=company.pk))
        if peers:
            IntercompanyTransaction.objects.get_or_create(
                source_company=company, transaction_number='IC-DEMO-001',
                defaults={'group': company.group, 'target_company': peers[0],
                          'transaction_date': today, 'tx_type': 'SALES',
                          'amount': 1_000, 'currency': usd, 'exchange_rate': 15_000,
                          'description': 'Penjualan antar company'},
            )

        self._monthly_trend(company, branch, cust_idr, supplier, idr, revenue, expense)

        self.stdout.write(f'  Demo {company.company_code}: {len(posted)} jurnal '
                          f'({", ".join(posted) or "tidak ada"})')

    @staticmethod
    def _shift_month(anchor, delta):
        """Geser tanggal beberapa bulan, aman untuk pergantian tahun."""
        total = (anchor.year * 12 + anchor.month - 1) + delta
        return date(total // 12, total % 12 + 1, 1)

    def _monthly_trend(self, company, branch, customer, supplier, currency, revenue_acc, expense_acc):
        """Satu invoice + satu bill per bulan selama 12 bulan.

        Tanpa ini semua jurnal demo ber tanggal di sekitar hari ini, jadi chart
        "Pendapatan vs Beban - 12 Bulan" hanya terisi 1-2 kolom. Nominal dibuat
        deterministik (naik bertahap) supaya aman dijalankan ulang.
        """
        from ap.models import SupplierBill, SupplierBillLine
        from ap.services import post_bill, recalc_bill_totals
        from ar.models import CustomerInvoice, CustomerInvoiceLine, InvoiceType
        from ar.services import post_invoice, recalc_invoice_totals

        today = timezone.localdate()
        posted = 0
        for i in range(11, -1, -1):
            month_start = self._shift_month(today.replace(day=1), -i)
            doc_date = date(month_start.year, month_start.month, 15)
            if doc_date > today:
                doc_date = today
            stamp = f'{doc_date:%Y%m}'
            rev_amount = 40_000_000 + i * 2_500_000
            exp_amount = 24_000_000 + i * 1_500_000

            try:
                inv, _ = CustomerInvoice.objects.get_or_create(
                    company=company, invoice_number=f'INV-TREND-{stamp}',
                    defaults={'invoice_date': doc_date,
                              'due_date': doc_date + timedelta(days=30),
                              'customer': customer, 'branch': branch, 'currency': currency,
                              'invoice_type': InvoiceType.INVOICE},
                )
                CustomerInvoiceLine.objects.get_or_create(
                    invoice=inv, line_no=1,
                    defaults={'description': f'Penjualan {doc_date:%b %Y}',
                              'quantity': 1, 'unit_price': rev_amount,
                              'account': revenue_acc},
                )
                recalc_invoice_totals(inv)
                inv.refresh_from_db()
                if inv.status != 'POSTED':
                    post_invoice(inv)
                    posted += 1
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f'  Tren {company.company_code} {stamp} revenue: {exc}'))

            try:
                bill, _ = SupplierBill.objects.get_or_create(
                    company=company, bill_number=f'BILL-TREND-{stamp}',
                    defaults={'bill_date': doc_date,
                              'due_date': doc_date + timedelta(days=30),
                              'supplier': supplier, 'branch': branch, 'currency': currency},
                )
                SupplierBillLine.objects.get_or_create(
                    bill=bill, line_no=1,
                    defaults={'description': f'Beban {doc_date:%b %Y}',
                              'quantity': 1, 'unit_price': exp_amount,
                              'account': expense_acc},
                )
                recalc_bill_totals(bill)
                bill.refresh_from_db()
                if bill.status != 'POSTED':
                    post_bill(bill)
                    posted += 1
            except Exception as exc:
                self.stdout.write(self.style.WARNING(
                    f'  Tren {company.company_code} {stamp} expense: {exc}'))

        self.stdout.write(f'  Tren 12 bulan {company.company_code}: {posted}/24 dokumen diposting')

    def _superuser(self, options):
        user, created = User.objects.get_or_create(
            username=options['username'],
            defaults={
                'email': options['superuser_email'],
                'role': User.Role.SUPER_ADMIN,
                'is_staff': True,
                'is_superuser': True,
                'first_name': 'Super',
                'last_name': 'Admin',
            },
        )
        if created:
            user.set_password(options['password'])
            user.save()
        elif not user.is_staff:
            user.is_staff = True
            user.is_superuser = True
            user.save(update_fields=['is_staff', 'is_superuser'])
        self.stdout.write(f'  Superuser siap: {user.username}')
        return user

    def _group(self):
        group, _ = Group.objects.get_or_create(
            code='GRP001', defaults={'name': 'Group Contoh', 'description': 'Holding multi-company'}
        )
        return group

    def _companies(self, group):
        """Satu company demo saja.

        Sebelumnya 3 company (A/B/C). Untuk contoh proses accounting/finance,
        satu company cukup: multi-company hanya menambah noise di report,
        switcher, dan intercompany. `_demo_data` sudah menjaga blok intercompany
        dengan `if peers:`, jadi tidak ada jurnal dengan target yang hilang.
        """
        specs = [
            ('A', 'PT Nusantara Jaya', 'IDR', 100),
        ]
        companies = []
        for code, name, currency, ownership in specs:
            company, _ = Company.objects.get_or_create(
                company_code=code,
                defaults={
                    'group': group,
                    'legal_name': name,
                    'display_name': name,
                    'base_currency': currency,
                    'fiscal_year_start': 1,
                    'ownership_percentage': ownership,
                    'npwp': f'01.234.567.8-{code}.000',
                    'email': f'{name.split()[1].lower()}@contoh.co.id',
                    'city': 'Jakarta',
                    'country': 'Indonesia',
                },
            )
            companies.append(company)
        return companies

    def _organization(self, company):
        from inventory.models import Warehouse

        hq, _ = Branch.objects.get_or_create(
            company=company, branch_code='HQ',
            defaults={'branch_name': 'Kantor Pusat'},
        )
        sby, _ = Branch.objects.get_or_create(
            company=company, branch_code='SBY',
            defaults={'branch_name': 'Branch Surabaya', 'status': 'ACTIVE'},
        )
        # Gudang per branch + set default_warehouse branch (PRD 6). Idempotent:
        # branch.default_warehouse hanya diisi kalau masih kosong.
        for branch in (hq, sby):
            warehouse, _ = Warehouse.objects.get_or_create(
                company=company, warehouse_code=f'GD-{branch.branch_code}',
                defaults={'warehouse_name': f'Gudang {branch.branch_name}',
                          'branch': branch, 'address': branch.address},
            )
            if branch.default_warehouse_id is None:
                branch.default_warehouse = warehouse
                branch.save(update_fields=['default_warehouse', 'updated_at'])
        departments = [('FIN', 'Finance'), ('SAL', 'Sales'), ('OPS', 'Operations'), ('ADM', 'Administration')]
        for code, name in departments:
            Department.objects.get_or_create(
                company=company, department_code=code, defaults={'department_name': name}
            )
        for code, name, dept_code in [('CC-FIN', 'Finance', 'FIN'), ('CC-SAL', 'Sales', 'SAL'),
                                      ('CC-OPS', 'Operations', 'OPS')]:
            CostCenter.objects.get_or_create(
                company=company, cost_center_code=code,
                defaults={'cost_center_name': name,
                          'department': Department.objects.filter(company=company,
                                                                   department_code=dept_code).first()},
            )

    def _tax_codes(self, company, accounts):
        specs = [
            ('PPN11', 'PPN 11%', 'OUTPUT', '11', accounts['2202']),
            ('PPN0', 'PPN 0%', 'OUTPUT', '0', accounts['2202']),
            ('PPNMAS', 'PPN Masukan 11%', 'INPUT', '11', accounts['2201']),
            ('PPH23', 'PPH 23%', 'WITHHOLDING', '2', accounts['2203']),
        ]
        for code, name, tax_type, rate, account in specs:
            TaxCode.objects.get_or_create(
                company=company, tax_code=code,
                defaults={'name': name, 'tax_type': tax_type, 'rate': rate, 'tax_account': account},
            )

    def _fiscal_year(self, company):
        today = timezone.localdate()
        year = today.year
        start = date(year, company.fiscal_year_start or 1, 1)
        end = date(year + 1, (company.fiscal_year_start or 1), 1) - timedelta(days=1)
        fiscal_year, created = FiscalYear.objects.get_or_create(
            company=company, name=str(year),
            defaults={'start_date': start, 'end_date': end},
        )
        generate_periods(fiscal_year)
        # fiscal year berikutnya, sudah OPEN, supaya posting tahun depan tidak blocked
        next_start = date(year + 1, company.fiscal_year_start or 1, 1)
        next_end = date(year + 2, company.fiscal_year_start or 1, 1) - timedelta(days=1)
        next_fy, _ = FiscalYear.objects.get_or_create(
            company=company, name=str(year + 1),
            defaults={'start_date': next_start, 'end_date': next_end},
        )
        generate_periods(next_fy)
        # Tahun sebelumnya juga dibuat: window chart 12 bulan terakhir bisa mulai
        # di November/Desember tahun lalu, dan posting butuh period OPEN.
        prev_start = date(year - 1, company.fiscal_year_start or 1, 1)
        prev_end = date(year, (company.fiscal_year_start or 1), 1) - timedelta(days=1)
        prev_fy, _ = FiscalYear.objects.get_or_create(
            company=company, name=str(year - 1),
            defaults={'start_date': prev_start, 'end_date': prev_end},
        )
        generate_periods(prev_fy)
        return fiscal_year