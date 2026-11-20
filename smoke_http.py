"""Smoke test HTTP: pastikan semua halaman admin & report render 200 tanpa error template."""
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from django.test import Client
from django.test.utils import override_settings, setup_test_environment
from django.urls import reverse

setup_test_environment()

from core.models import User

PASS, FAIL = [], []


def check(name, code, detail=''):
    ok = code == 200
    (PASS if ok else FAIL).append(f'{name} ({code})')
    print(f'[{"PASS" if ok else "FAIL"}] {name}  [{code}]{" " + detail if detail else ""}')


def expect(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    print(f'[{"PASS" if condition else "FAIL"}] {name}{"  -> " + str(detail) if detail else ""}')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost', '127.0.0.1'])
def main():
    client = Client()
    user = User.objects.get(username='admin')
    client.force_login(user)

    urls = {
        'Root redirect ke admin': '/',        'Admin index': reverse('admin:index'),
        'Dashboard KPI': reverse('reports_dashboard'),
        'Trial Balance': reverse('reports_trial_balance'),
        'Profit & Loss': reverse('reports_profit_loss'),
        'Balance Sheet': reverse('reports_balance_sheet'),
        'Cash Flow': reverse('reports_cash_flow'),
        'General Ledger': reverse('reports_general_ledger'),
        'Comparative Balance': reverse('reports_balance_comparison'),
        'AR/AP Aging': reverse('reports_aging'),
        'Company Comparison': reverse('reports_company_comparison'),
        'GL - Chart of Accounts': reverse('admin:gl_account_changelist'),
        'GL - Journal Entry': reverse('admin:gl_journalentry_changelist'),
        'GL - Input Jurnal Umum': reverse('admin:gl_journalentry_add'),
        'GL - Accounting Period': reverse('admin:gl_accountingperiod_changelist'),
        'GL - Fiscal Year': reverse('admin:gl_fiscalyear_changelist'),
        'GL - Account Mapping': reverse('admin:gl_accountmapping_changelist'),
        'AR - Customer': reverse('admin:ar_customer_changelist'),
        'AR - Customer Invoice': reverse('admin:ar_customerinvoice_changelist'),
        'AR - Customer Payment': reverse('admin:ar_customerpayment_changelist'),
        'AP - Supplier': reverse('admin:ap_supplier_changelist'),
        'AP - Supplier Bill': reverse('admin:ap_supplierbill_changelist'),
        'AP - Supplier Payment': reverse('admin:ap_supplierpayment_changelist'),
        'Bank - Bank Account': reverse('admin:bank_bankaccount_changelist'),
        'Bank - Bank Transaction': reverse('admin:bank_banktransaction_changelist'),
        'Bank - Reconciliation': reverse('admin:bank_bankreconciliation_changelist'),
        'Core - Group': reverse('admin:core_group_changelist'),
        'Core - Company': reverse('admin:core_company_changelist'),
        'Core - Branch': reverse('admin:core_branch_changelist'),
        'Core - Department': reverse('admin:core_department_changelist'),
        'Core - Cost Center': reverse('admin:core_costcenter_changelist'),
        'Core - Product': reverse('admin:core_product_changelist'),
        'Core - Project': reverse('admin:core_project_changelist'),
        'Core - Project Cost': reverse('admin:core_projectcost_changelist'),
        'Core - Project Milestone': reverse('admin:core_projectmilestone_changelist'),
        'Core - Project Billing': reverse('admin:core_projectbilling_changelist'),
        'Report - Project Profitability': reverse('reports_project_profitability'),
        'Notify - Notification': reverse('admin:notify_notification_changelist'),
        'Core - Budget': reverse('admin:core_budget_changelist'),
        'Report - Budget vs Actual': reverse('reports_budget_vs_actual'),
        'Report - Cost Accounting': reverse('reports_cost_accounting'),
        'Report - Product Profitability': reverse('reports_product_profitability'),
        'Report - Customer Statement': reverse('reports_customer_statement'),
        'Report - Vendor Statement': reverse('reports_vendor_statement'),
        'Report - Jurnal per Kategori': reverse('reports_journal_by_category'),
        'Report - Consolidated': reverse('reports_consolidated'),
        'Core - Tax Code': reverse('admin:core_taxcode_changelist'),
        'Core - Currency': reverse('admin:core_currency_changelist'),
        'Core - Exchange Rate': reverse('admin:core_exchangerate_changelist'),
        'Core - Approval Limit': reverse('admin:core_approvallimit_changelist'),
        'Core - Audit Log': reverse('admin:core_auditlog_changelist'),
        'Core - User': reverse('admin:core_user_changelist'),
        'FA - Asset Category': reverse('admin:fa_assetcategory_changelist'),
        'FA - Fixed Asset': reverse('admin:fa_fixedasset_changelist'),
        'FA - Depreciation': reverse('admin:fa_assetdepreciation_changelist'),
        'FA - Asset Transaction': reverse('admin:fa_assettransaction_changelist'),
        'Inventory - Inventory': reverse('admin:inventory_inventory_changelist'),
        'Inventory - Transaction': reverse('admin:inventory_inventorytransaction_changelist'),
        'Inventory - Cost Layer': reverse('admin:inventory_inventorycost_changelist'),
        'Workflow - Approval Workflow': reverse('admin:workflow_approvalworkflow_changelist'),
        'Workflow - Approval Request': reverse('admin:workflow_approvalrequest_changelist'),
        'Consolidation - Intercompany': reverse('admin:consolidation_intercompanytransaction_changelist'),
        'Consolidation - Consolidation Run': reverse('admin:consolidation_consolidationrun_changelist'),
    }

    for name, url in urls.items():
        try:
            response = client.get(url, follow=(name != 'Root redirect ke admin'))
            if name == 'Root redirect ke admin':
                ok = response.status_code == 302 and '/admin/' in response.get('Location', '')
                code = 200 if ok else response.status_code
            else:
                code = response.status_code
            check(name, code)
        except Exception as exc:
            (PASS if False else FAIL).append(f'{name}: {exc}')
            print(f'[FAIL] {name}  EXCEPTION: {type(exc).__name__}: {exc}')

    print()
    _check_company_switch(client)
    print()
    print(f'TOTAL: {len(PASS)} pass, {len(FAIL)} fail')
    for f in FAIL:
        print('  FAILED:', f)
    return 1 if FAIL else 0


def _check_company_switch(client):
    """Company switcher harus benar-benar mempersempit data, bukan cuma session."""
    from core.models import Company
    from gl.models import JournalEntry

    def probe(url):
        cl = client.get(url).context['cl']
        return set(o.company.company_code for o in cl.result_list), cl.result_list.count()

    companies = list(Company.objects.all()[:3])
    if len(companies) < 2:
        print('[SKIP] company switcher (butuh >= 2 company)')
        return

    url = reverse('admin:gl_journalentry_changelist')

    client.get(reverse('core_switch_company'))
    all_codes, all_rows = probe(url)
    expect(f'Company switcher: tanpa scope semua company terlihat ({all_rows} rows)',
           all_rows == JournalEntry.objects.count(), ', '.join(sorted(all_codes)))

    for company in companies:
        client.get(f'{reverse("core_switch_company")}?company_id={company.pk}')
        codes, rows = probe(url)
        expected = JournalEntry.objects.filter(company=company).count()
        expect(f'Company switcher: scope {company.company_code} hanya company itu ({rows} rows)',
               codes == {company.company_code} and rows == expected, ', '.join(sorted(codes)))

    client.get(reverse('core_switch_company'))
    codes, rows = probe(url)
    expect(f'Company switcher: clear kembali ke semua ({rows} rows)',
           rows == JournalEntry.objects.count(), ', '.join(sorted(codes)))


if __name__ == '__main__':
    raise SystemExit(main())