"""Master Warehouse (gudang) + sinkronisasinya ke Branch dan Inventory.

Dulu `Inventory.warehouse` cuma CharField bebas dan tidak ada list gudang.
Test ini mengunci: Warehouse company-scoped di admin, bisa dipakai Inventory,
dan Branch.default_warehouse bisa menunjuk gudang itu.
"""

from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from core.models import Branch, Company, Group, Product, User, UserCompanyAccess
from gl.models import Account, AccountCategory
from .models import Inventory, InventoryTransaction, StockOpname, Warehouse


class WarehouseTests(TestCase):
    def setUp(self):
        group = Group.objects.create(name='Grup', code='G')
        self.company = Company.objects.create(
            company_code='A', legal_name='PT A', display_name='PT A', group=group)
        self.other = Company.objects.create(
            company_code='B', legal_name='PT B', display_name='PT B', group=group)
        self.branch = Branch.objects.create(
            company=self.company, branch_code='HQ', branch_name='Kantor Pusat')
        self.warehouse = Warehouse.objects.create(
            company=self.company, branch=self.branch,
            warehouse_code='GD-HQ', warehouse_name='Gudang Pusat')
        self.other_warehouse = Warehouse.objects.create(
            company=self.other, warehouse_code='GD-B', warehouse_name='Gudang B')

        self.user = User.objects.create_user('wh', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        self.user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='inventory',
            codename__in=['view_warehouse', 'view_inventory']))
        self.client.force_login(self.user)

    def _account(self, code, category):
        return Account.objects.create(
            company=self.company, account_code=code, account_name=f'Akun {code}',
            account_category=category, account_type='X', normal_balance='D', is_postable=True)

    def test_admin_inventory_menampilkan_list_gudang(self):
        response = self.client.get('/admin/inventory/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('admin:inventory_warehouse_changelist'))

    def test_changelist_gudang_company_scoped(self):
        response = self.client.get(reverse('admin:inventory_warehouse_changelist'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'GD-HQ')
        self.assertNotContains(response, 'GD-B')

    def test_branch_default_warehouse_dan_inventory_pakai_fk(self):
        self.branch.default_warehouse = self.warehouse
        self.branch.save(update_fields=['default_warehouse'])

        product = Product.objects.create(
            company=self.company, product_code='P1', product_name='Produk 1',
            revenue_account=self._account('4101', AccountCategory.REVENUE),
            cogs_account=self._account('5101', AccountCategory.COGS))
        inventory = Inventory.objects.create(
            company=self.company, branch=self.branch, warehouse=self.warehouse, product=product)

        self.branch.refresh_from_db()
        self.assertEqual(self.branch.default_warehouse, self.warehouse)
        self.assertEqual(inventory.warehouse, self.warehouse)
        self.assertIn(inventory, self.warehouse.inventories.all())


class StockOpnameTests(TestCase):
    """Stock opname: snapshot qty sistem, lalu posting variance ke Inventory."""

    def setUp(self):
        group = Group.objects.create(name='Grup2', code='G2')
        self.company = Company.objects.create(
            company_code='A2', legal_name='PT A2', display_name='PT A2', group=group)
        self.warehouse = Warehouse.objects.create(
            company=self.company, warehouse_code='GD-A2', warehouse_name='Gudang A2')
        self.user = User.objects.create_user('op', password='pw', is_staff=True)
        UserCompanyAccess.objects.create(user=self.user, company=self.company)
        revenue = Account.objects.create(
            company=self.company, account_code='4101', account_name='Penjualan',
            account_category=AccountCategory.REVENUE, account_type='X', normal_balance='C',
            is_postable=True)
        cogs = Account.objects.create(
            company=self.company, account_code='5101', account_name='HPP',
            account_category=AccountCategory.COGS, account_type='X', normal_balance='D',
            is_postable=True)
        self.product = Product.objects.create(
            company=self.company, product_code='P-OP', product_name='Produk Opname',
            revenue_account=revenue, cogs_account=cogs)
        from .services import goods_receipt
        goods_receipt(self.product, 100, 5000, reference='AWAL')
        self.client.force_login(self.user)

    def _opname(self, counted):
        from .models import StockOpname, StockOpnameLine
        from .services import refresh_system_quantity
        opname = StockOpname.objects.create(
            company=self.company, warehouse=self.warehouse, opname_number='SO-001',
            opname_date='2026-10-01')
        inv = Inventory.objects.get(product=self.product)
        StockOpnameLine.objects.create(
            opname=opname, inventory=inv, counted_quantity=counted)
        refresh_system_quantity(opname)
        return opname

    def test_posting_menyesuaikan_stok_dan_membuat_adjustment(self):
        from .models import StockOpname, InventoryTransaction
        from .services import post_stock_opname

        opname = self._opname(counted=97)
        line = opname.lines.get()
        self.assertEqual(line.system_quantity, 100)
        self.assertEqual(line.variance, -3)

        post_stock_opname(opname, user=self.user)

        opname.refresh_from_db()
        inv = Inventory.objects.get(product=self.product)
        self.assertEqual(opname.status, StockOpname.Status.POSTED)
        self.assertEqual(opname.posted_by, self.user)
        self.assertEqual(inv.quantity_on_hand, 97)
        tx = InventoryTransaction.objects.get(reference='SO-001')
        self.assertEqual(tx.tx_type, InventoryTransaction.TxType.ADJUSTMENT_OUT)
        self.assertEqual(tx.quantity, 3)

    def test_tidak_bisa_posting_dua_kali(self):
        from django.core.exceptions import ValidationError
        from .services import post_stock_opname

        opname = self._opname(counted=98)
        post_stock_opname(opname, user=self.user)
        with self.assertRaises(ValidationError):
            post_stock_opname(opname, user=self.user)
        self.assertEqual(Inventory.objects.get(product=self.product).quantity_on_hand, 98)


class DemoInventorySeedTests(TestCase):
    """Sample seed persediaan: terisi dan idempotent (tidak gandakan stok)."""

    def test_demo_inventory_idempotent(self):
        from datetime import date

        from core.management.commands.seed_data import Command

        group = Group.objects.create(name='Grup3', code='G3')
        company = Company.objects.create(
            company_code='A3', legal_name='PT A3', display_name='PT A3', group=group)
        Warehouse.objects.create(company=company, warehouse_code='GD-A3', warehouse_name='Gudang A3')
        accounts = {}
        for code, category, balance in [('1301', AccountCategory.ASSET, 'D'),
                                        ('4101', AccountCategory.REVENUE, 'C'),
                                        ('5101', AccountCategory.COGS, 'D')]:
            accounts[code] = Account.objects.create(
                company=company, account_code=code, account_name=f'Akun {code}',
                account_category=category, account_type='X', normal_balance=balance,
                is_postable=True)

        command = Command()
        for _ in range(2):
            command._demo_inventory(company, accounts, accounts['4101'], accounts['5101'], date.today())

        self.assertEqual(Product.objects.filter(company=company).count(), 2)
        self.assertEqual(Inventory.objects.filter(company=company).count(), 2)
        self.assertEqual(InventoryTransaction.objects.count(), 4, 'GR/GI tidak boleh dobel')
        self.assertEqual(StockOpname.objects.count(), 1)
        opname = StockOpname.objects.get()
        self.assertEqual(opname.lines.count(), 2)
        self.assertEqual(opname.lines.order_by('id').first().variance, -2)

        first = Inventory.objects.get(product__product_code='PRD-DEMO1')
        self.assertEqual(first.quantity_on_hand, 75)  # 100 masuk - 25 keluar
