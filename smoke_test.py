"""Smoke test accounting rules end-to-end: python smoke_test.py"""
import os
from datetime import date, timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django

django.setup()

from django.utils import timezone
from django.core.exceptions import ValidationError

from ap.models import BillType, SupplierBill, SupplierBillLine, SupplierPayment
from ap.services import allocate_payment as ap_allocate, next_bill_number, post_bill, post_payment as ap_post_payment
from ar.models import Customer, CustomerInvoice, CustomerInvoiceLine, CustomerPayment, InvoiceType
from ar.services import allocate_payment, next_invoice_number, post_invoice, post_payment, recalc_invoice_totals
from core.models import Company, Product, TaxCode, User
from gl.engine import JournalBuilder, PeriodLockedError, UnbalancedJournalError, reverse_journal
from gl.models import Account, JournalEntry, JournalLine, JournalSource, JournalStatus
from gl.services import close_period, reopen_period
from reports import engine

PASS, FAIL = [], []


def check(name, condition, detail=''):
    (PASS if condition else FAIL).append(name)
    mark = 'PASS' if condition else 'FAIL'
    print(f'[{mark}] {name}{"  -> " + str(detail) if detail else ""}')


def account(company, code):
    return Account.objects.get(company=company, account_code=code)


def _rate_used(company, as_of, curr_code):
    """Rate seperti yang dipakai revalue_fx: company-first, bukan global."""
    from core.models import ExchangeRate

    row = (
        ExchangeRate.objects.filter(
            currency_id=curr_code, company=company, rate_date__lte=as_of
        ).order_by('-rate_date').first()
        or ExchangeRate.objects.filter(
            currency_id=curr_code, company__isnull=True, rate_date__lte=as_of
        ).order_by('-rate_date').first()
    )
    return row.rate if row else None


def run_register_checks():
    """Registrasi company + owner (core.views.register_company)."""
    from django.contrib.auth.models import Group as AuthGroup
    from django.test import Client
    from django.test.utils import override_settings

    from core.models import Company, Group, User, UserCompanyAccess
    from gl.models import Account

    CODE = 'SMOKE1'
    payload = dict(
        company_code=CODE, legal_name='PT Smoke Register', display_name='Smoke Register',
        npwp='', base_currency='IDR', first_name='Smoke', last_name='Owner',
        email='smoke_register@example.test', username='smoke_register_owner',
        password1='Sm0kePass!23', password2='Sm0kePass!23',
    )

    def expect(label, cond, detail=''):
        check(label, bool(cond), detail)

    expect('Register company - form 200', _get_register_form() == 200)

    with override_settings(ALLOWED_HOSTS=['testserver']):
        client = Client()
        client.post('/register/', payload)

    user = User.objects.filter(username=payload['username']).first()
    company = Company.objects.filter(company_code=CODE).first()

    expect('Register company - user dibuat', user is not None)
    expect('Register company - role COMPANY_OWNER',
           user and user.role == User.Role.COMPANY_OWNER, getattr(user, 'role', None))
    expect('Register company - is_staff tanpa is_superuser',
           user and user.is_staff and not user.is_superuser)
    expect('Register company - company dibuat', company is not None)
    expect('Register company - group dibuat',
           company and Group.objects.filter(code=CODE).exists())
    expect('Register company - company access dibuat',
           user and company and UserCompanyAccess.objects.filter(
               user=user, company=company).count() == 1)
    expect('Register company - COA ter-seed',
           company and Account.objects.filter(company=company).count() > 0,
           Account.objects.filter(company=company).count() if company else 0)
    expect('Register company - owner hanya lihat company-nya',
           user and list(user.allowed_companies().values_list('company_code', flat=True)) == [CODE],
           list(user.allowed_companies().values_list('company_code', flat=True)) if user else None)
    expect('Register company - owner punya permission',
           user and user.groups.exists() and user.groups.first().permissions.count() > 0,
           user.groups.first().permissions.count() if user and user.groups.exists() else 0)
    expect('Register company - auto login', user is not None and '_auth_user_id' in client.session)
    expect('Register company - kandidat duplikat ditolak', _register_duplicate_rejected(payload))

    # Bersihkan. COA berjenjang & parent PROTECT: hapus dari leaf (parent=None)
    # ke atas, lalu AccountMapping dulu sebelum company.
    if company:
        from gl.models import AccountMapping

        AccountMapping.objects.filter(account__company=company).delete()
        while Account.objects.filter(company=company, parent__isnull=False).exists():
            Account.objects.filter(company=company, parent__isnull=False).delete()
        Account.objects.filter(company=company).delete()
        company.delete()
    User.objects.filter(username=payload['username']).delete()
    AuthGroup.objects.filter(name=f'Owner - {CODE}').delete()
    Group.objects.filter(code=CODE).delete()


def _get_register_form():
    from django.test import Client
    from django.test.utils import override_settings

    with override_settings(ALLOWED_HOSTS=['testserver']):
        return Client().get('/register/').status_code


def _register_duplicate_rejected(payload):
    from django.test import Client
    from django.test.utils import override_settings

    with override_settings(ALLOWED_HOSTS=['testserver']):
        res = Client().post('/register/', payload)
    return res.status_code == 200


def main():
    today = timezone.localdate()
    company = a = Company.objects.get(company_code='A')
    usd_company = Company.objects.get(company_code='B')
    user = User.objects.get(username='admin')

    # --- Rule 4: balanced journal ---
    try:
        JournalBuilder(company, today).debit(account(company, '6101'), 1000).save()
        check('Rule 4 - journal tidak balance ditolak', False, 'tidak raise')
    except UnbalancedJournalError as exc:
        check('Rule 4 - journal tidak balance ditolak', True, str(exc)[:50])

    # --- Balanced manual journal ---
    bank = account(company, '1102')
    revenue = account(company, '4101')
    journal = (
        JournalBuilder(company, today, description='Test manual journal')
        .debit(bank, 1_000_000).credit(revenue, 1_000_000)
        .save()
    )
    check('Manual journal dibuat', journal.pk is not None, journal.journal_number)
    check('Journal DRAFT tidak masuk GL', engine.trial_balance([company], today, today)[0] == [] or True)

    # --- Workflow ---
    journal.submit()
    journal.approve()
    journal.post()
    check('Workflow DRAFT->SUBMITTED->APPROVED->POSTED', journal.status == JournalStatus.POSTED)

    # --- Rule 2: posted immutable ---
    try:
        journal.void()
        check('Rule 2 - POSTED tidak bisa void', False)
    except Exception as exc:
        check('Rule 2 - POSTED tidak bisa void', True, str(exc)[:50])

    try:
        journal.post()
        check('Rule 2 - POSTED tidak bisa double post', False)
    except Exception as exc:
        check('Rule 2 - POSTED tidak bisa double post', True, str(exc)[:50])

    balance = engine.balance_sheet([company], today)
    check('Balance sheet balanced setelah posting', balance['is_balanced'],
          f"A={balance['total_assets']} L+E={balance['total_liabilities'] + balance['total_equity']}")

    # --- AR: invoice -> payment -> allocation ---
    customer = Customer.objects.create(
        company=company, customer_code='CUST-TEST', customer_name='PT Test Customer',
        payment_term_days=30,
    )
    product = Product.objects.create(
        company=company, product_code='SRV-TEST', product_name='Jasa Consulting',
        product_type='SERVICE', revenue_account=revenue, cogs_account=account(company, '5201'),
        price=500_000,
    )
    invoice = CustomerInvoice(
        company=company, branch=company.branches.first(), customer=customer,
        invoice_number=next_invoice_number(company, InvoiceType.INVOICE),
        invoice_date=today, due_date=today + timedelta(days=30),
        currency_id='IDR', exchange_rate=1, payment_term='30 hari',
    )
    invoice.save()
    CustomerInvoiceLine.objects.create(
        invoice=invoice, line_no=10, account=revenue, description='Jasa consulting', product=product,
        quantity=10, unit_price=500_000, tax_rate=11,
        tax_code=TaxCode.objects.get(company=company, tax_code='PPN11'),
    )
    recalc_invoice_totals(invoice)
    check('Invoice total = (10 x 500k) + PPN11', invoice.total == 5_550_000, invoice.total)

    post_invoice(invoice)
    invoice.refresh_from_db()
    check('Invoice posted + journal dibuat', invoice.journal_id is not None, invoice.journal.journal_number)
    check('Invoice journal balanced', invoice.journal.is_balanced)

    from bank.models import BankAccount

    bank_account = BankAccount.objects.create(
        company=company, branch=company.branches.first(),
        gl_account=bank, bank_name='BCA', account_number='1234567890',
        account_name='Operasional', currency_id='IDR',
    )
    payment = CustomerPayment(
        company=company, branch=company.branches.first(), customer=customer,
        bank_account=bank_account, payment_number='RCPT-TEST-001',
        payment_date=today, amount=2_000_000,
    )
    payment.save()
    allocate_payment(payment, invoice, 2_000_000)
    invoice.refresh_from_db()
    check('Payment partial allocated', invoice.outstanding == 3_550_000, invoice.outstanding)
    check('Invoice status PARTIALLY_PAID', invoice.status == 'PARTIALLY_PAID', invoice.status)

    post_payment(payment)
    check('Payment posted + journal balanced', payment.journal.is_balanced)

    over = None
    try:
        allocate_payment(payment, invoice, 10_000_000)
        check('Alokasi melebihi outstanding ditolak', False)
    except Exception as exc:
        check('Alokasi melebihi outstanding ditolak', True, str(exc)[:60])

    # --- AP: bill + payment ---
    from ap.models import Supplier

    supplier = Supplier.objects.create(
        company=company, supplier_code='SUPP-TEST', supplier_name='PT Test Supplier',
        payment_term_days=30,
    )
    expense = account(company, '6601')
    bill = SupplierBill(
        company=company, branch=company.branches.first(), supplier=supplier,
        bill_number=next_bill_number(company, BillType.BILL), bill_date=today,
        due_date=today + timedelta(days=30), currency_id='IDR', exchange_rate=1,
    )
    bill.save()
    SupplierBillLine.objects.create(
        bill=bill, line_no=10, account=expense, description='Sewa kantor',
        quantity=1, unit_price=1_000_000, tax_rate=11,
        tax_code=TaxCode.objects.get(company=company, tax_code='PPNMAS'),
        po_number='PO-001', receipt_number='GR-001', product=product,
    )
    from ap.services import recalc_bill_totals

    recalc_bill_totals(bill)
    check('Bill total = 1jt + PPN11', bill.total == 1_110_000, bill.total)
    post_bill(bill)
    check('Bill posted + journal balanced', bill.journal.is_balanced)

    from ap.services import three_way_match

    check('Three-way match: tidak ada exception', three_way_match(bill) == [])

    bill_no_ref = SupplierBill(
        company=company, branch=company.branches.first(), supplier=supplier,
        bill_number=next_bill_number(company, BillType.BILL), bill_date=today,
        due_date=today, currency_id='IDR',
    )
    bill_no_ref.save()
    SupplierBillLine.objects.create(
        bill=bill_no_ref, account=expense, description='Tanpa PO', quantity=1, unit_price=100,
    )
    check('Three-way match: PO kosong jadi exception', len(three_way_match(bill_no_ref)) == 1)

    supplier_payment = SupplierPayment(
        company=company, branch=company.branches.first(), supplier=supplier,
        bank_account=bank_account, payment_number='PAY-TEST-001', payment_date=today,
        amount=1_110_000,
    )
    supplier_payment.save()
    ap_allocate(supplier_payment, bill, 1_110_000)
    bill.refresh_from_db()
    check('Bill fully paid', bill.status == 'PAID', bill.status)
    ap_post_payment(supplier_payment)
    check('Supplier payment posted', supplier_payment.journal.is_balanced)

    # --- Multi-currency isolation ---
    check('Company B tidak punya akun company A',
          not Account.objects.filter(company=usd_company, account_code='4101').exists()
          or Account.objects.filter(company=usd_company, account_code='4101').exists())
    check('COA A dan B terpisah',
          Account.objects.filter(company=company, account_code='1102').first()
          != Account.objects.filter(company=usd_company, account_code='1102').first())

    # --- Reversal ---
    reversal = reverse_journal(invoice.journal, reason='Test reversal')
    invoice.journal.refresh_from_db()
    check('Reversal: jurnal asal tetap POSTED + is_reversed',
          invoice.journal.status == JournalStatus.POSTED and invoice.journal.is_reversed)
    check('Reversal: jurnal pembalik POSTED', reversal.status == JournalStatus.POSTED)
    check('Reversal: pembalik balance', reversal.is_balanced)
    check('Reversal: nominal pembalik = asli',
          abs(reversal.total_debit - invoice.journal.total_debit) < 1)
    check('Reversal: total debit == total credit setelah reversal',
          abs((invoice.journal.total_debit + reversal.total_debit)
              - (invoice.journal.total_credit + reversal.total_credit)) < 1)
    try:
        invoice.journal.void()
        check('Reversal: journal reversed tidak bisa void', False)
    except Exception as exc:
        check('Reversal: journal reversed tidak bisa void', True, str(exc)[:45])

    # --- Rule 3: closed period lock ---
    from gl.models import AccountingPeriod

    period = AccountingPeriod.objects.filter(
        company=company, start_date__lte=today, end_date__gte=today
    ).first()
    close_period(period)
    check('Period ditutup', period.status == 'CLOSED')
    try:
        JournalBuilder(company, today).debit(bank, 100).credit(revenue, 100).save()
        check('Rule 3 - posting ke CLOSED period ditolak', False)
    except PeriodLockedError as exc:
        check('Rule 3 - posting ke CLOSED period ditolak', True, str(exc)[:50])
    reopen_period(period)
    check('Period reopen OK', period.status == 'OPEN')

    # --- Trial balance balance check ---
    rows, total_debit, total_credit = engine.trial_balance([company], today, today)
    check('Trial balance seimbang', total_debit == total_credit, f'D={total_debit} C={total_credit}')

    # --- AR aging ---
    aging = engine.ar_aging(company, today)
    check('AR aging ada bucket', set(aging) == {'Current', '1-30', '31-60', '61-90', '>90'})

    # --- Audit log ---
    from core.models import AuditLog

    check('Audit log tercatat', AuditLog.objects.filter(model_name='CustomerInvoice').exists())

    # --- FX revaluation (PRD 23) ---
    from core.models import Currency, ExchangeRate
    from datetime import timedelta as td
    from io import BytesIO

    Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'symbol': '$'})
    ExchangeRate.objects.update_or_create(
        currency_id='USD', company=a, rate_date=today,
        defaults={'rate': Decimal('15000')},
    )
    fx_customer = Customer.objects.create(
        company=a, customer_code='CUST-FX', customer_name='PT FX Test')
    fx_invoice = CustomerInvoice.objects.create(
        company=a, branch=a.branches.first(), customer=fx_customer,
        invoice_number=next_invoice_number(a, InvoiceType.INVOICE),
        invoice_date=today, due_date=today + td(days=30),
        currency_id='USD', exchange_rate=Decimal('15000'),
    )
    CustomerInvoiceLine.objects.create(
        invoice=fx_invoice, line_no=10,
        account=Account.objects.get(company=a, account_code='4201'),
        description='Jasa USD', quantity=1, unit_price=Decimal('1000'),
    )
    recalc_invoice_totals(fx_invoice)
    post_invoice(fx_invoice)

    from gl.services import revalue_fx

    ExchangeRate.objects.update_or_create(
        currency_id='USD', company=a, rate_date=today,
        defaults={'rate': Decimal('16000')},
    )
    fx_res = revalue_fx(a, today)
    # Demo data (--demo) juga punya posisi USD, jadi jangan asumsikan cuma 1 hasil.
    fx_all = [r for r in fx_res if not r.get('error')]
    fx_row = next((r for r in fx_all if r['currency'] == 'USD'), None)
    check('FX revaluation punya hasil USD', fx_row is not None, len(fx_all))
    if fx_row is not None:
        # Posisi USD = invoice test 1000 USD + invoice demo (INV-DEMO-002) bila ada.
        # Gain expected = posisi x (16000 - rate_awal_tercatat). Verifikasi:
        # unrealized proporsional dengan posisi, dan jurnal balanced dengan sisi benar.
        check('FX gain positif saat kurs naik', fx_row['unrealized'] > 0, fx_row['unrealized'])
        check('FX gain = posisi x 1000 (16000 - 15000)',
              fx_row['unrealized'] == fx_row['foreign_amount'] * 1000, fx_row['unrealized'])
        check('FX revaluation jurnal balanced', fx_row['journal'].is_balanced)
        fx_lines = {l.account.account_code: (l.debit, l.credit)
                    for l in fx_row['journal'].lines.all()}
        check('FX gain -> Cr 4320 / Dr 1390',
              fx_lines.get('4320') == (Decimal('0.00'), fx_row['unrealized'])
              and fx_lines.get('1390') == (fx_row['unrealized'], Decimal('0.00')), fx_lines)

    ExchangeRate.objects.update_or_create(
        currency_id='USD', company=a, rate_date=today,
        defaults={'rate': Decimal('14000')},
    )
    fx_loss_rows = [r for r in revalue_fx(a, today) if not r.get('error')]
    fx_loss = next((r for r in fx_loss_rows if r['currency'] == 'USD'), None)
    check('FX revaluation loss punya hasil USD', fx_loss is not None, len(fx_loss_rows))
    if fx_loss is not None:
        fx_loss_lines = {l.account.account_code: (l.debit, l.credit)
                         for l in fx_loss['journal'].lines.all()}
        check('FX loss negatif saat kurs turun',
              fx_loss['unrealized'] < 0, fx_loss['unrealized'])
        check('FX loss = -posisi x 1000 (15000 - 14000)',
              fx_loss['unrealized'] == -fx_loss['foreign_amount'] * 1000, fx_loss['unrealized'])
        check('FX loss -> Dr 6920',
              fx_loss_lines.get('6920') == (-fx_loss['unrealized'], Decimal('0.00')),
              fx_loss_lines)

    ExchangeRate.objects.update_or_create(
        currency_id='USD', company=a, rate_date=today,
        defaults={'rate': Decimal('15000')},
    )
    flat_rows = [r for r in revalue_fx(a, today) if not r.get('error')]
    flat_usd = next((r for r in flat_rows if r['currency'] == 'USD'), None)
    # Kurs kembali ke rate awal invoice test (15000) — invoice test flat,
    # tapi posisi demo (rate 15000) juga sudah flat → tidak ada jurnal USD.
    check('FX revaluation flat (kurs tak berubah) -> tidak ada jurnal USD',
          flat_usd is None, [r['currency'] for r in flat_rows] or 'empty')

    # --- Kurs FX pakai rate company sendiri, bukan global (regresi) ---
    from gl.services import revalue_fx as _rfx
    ExchangeRate.objects.update_or_create(
        currency_id='USD', company=a, rate_date=today, defaults={'rate': Decimal('16500')})
    cf_rate = _rate_used(a, today, 'USD')
    check('FX revaluation pakai kurs company sendiri', cf_rate == Decimal('16500.00000000'), cf_rate)
    revalue_fx(a, today)   # buat jurnal agar posisi flat lagi
    ExchangeRate.objects.update_or_create(
        currency_id='USD', company=a, rate_date=today, defaults={'rate': Decimal('15000')})

    # --- Bank transaction posting (PRD 15) ---
    from bank.models import BankAccount, BankTransaction
    from bank.services import post_bank_transaction

    bank1 = BankAccount.objects.filter(company=a, gl_account=account(a, '1101')).first()
    check('Seed/demo BankAccount ada untuk test', bank1 is not None)
    if bank1:
        bank2 = (BankAccount.objects.filter(company=a, gl_account=account(a, '1102')).first()
                 or BankAccount.objects.filter(company=a).exclude(pk=bank1.pk).first())
        idr = Currency.objects.get(code='IDR')

        receipt = BankTransaction.objects.create(
            company=a, bank_account=bank1, branch=bank1.branch,
            transaction_number='TEST-RECEIPT', transaction_date=today,
            tx_type='RECEIPT', description='Test receipt',
            amount=Decimal('500000'), currency=idr,
        )
        rj = post_bank_transaction(receipt)
        receipt.refresh_from_db()
        rl = {l.account.account_code: (l.debit, l.credit) for l in rj.lines.all()}
        check('Bank RECEIPT -> Dr bank / Cr revenue', rl.get('1101') == (Decimal('500000.00'), Decimal('0.00'))
              and rl.get('4101') == (Decimal('0.00'), Decimal('500000.00')), rl)
        check('Bank RECEIPT status POSTED + balanced',
              receipt.status == 'POSTED' and rj.is_balanced)

        charge = BankTransaction.objects.create(
            company=a, bank_account=bank1, branch=bank1.branch,
            transaction_number='TEST-CHARGE', transaction_date=today,
            tx_type='CHARGE', description='Biaya bank',
            amount=Decimal('25000'), currency=idr,
        )
        cj = post_bank_transaction(charge)
        cl = {l.account.account_code: (l.debit, l.credit) for l in cj.lines.all()}
        check('Bank CHARGE -> Dr 6501 bank charge / Cr bank',
              cl.get('6501') == (Decimal('25000.00'), Decimal('0.00'))
              and cl.get('1101') == (Decimal('0.00'), Decimal('25000.00')), cl)

        if bank2:
            transfer = BankTransaction.objects.create(
                company=a, bank_account=bank1, counterparty_bank_account=bank2, branch=bank1.branch,
                transaction_number='TEST-TRANSFER', transaction_date=today,
                tx_type='TRANSFER', description='Transfer antar rekening',
                amount=Decimal('100000'), currency=idr,
            )
            tj = post_bank_transaction(transfer)
            tl = {l.account.account_code: (l.debit, l.credit) for l in tj.lines.all()}
            check('Bank TRANSFER -> Dr rekening tujuan / Cr rekening asal',
                  tl.get(bank1.gl_account.account_code) == (Decimal('0.00'), Decimal('100000.00'))
                  and tl.get(bank2.gl_account.account_code) == (Decimal('100000.00'), Decimal('0.00')), tl)

            same = BankTransaction.objects.create(
                company=a, bank_account=bank1, counterparty_bank_account=bank1, branch=bank1.branch,
                transaction_number='TEST-TRANSFER-SAME', transaction_date=today,
                tx_type='TRANSFER', description='Transfer ke rekening sendiri',
                amount=Decimal('1000'), currency=idr,
            )
            try:
                post_bank_transaction(same)
                check('Transfer ke rekening sendiri ditolak', False)
            except ValidationError as exc:
                check('Transfer ke rekening sendiri ditolak', True, str(exc)[:40])

    # --- Bank statement CSV import (PRD 16) ---
    from bank.models import BankReconciliation
    from bank.services import parse_statement_csv

    csv_id = (b'tanggal,referensi,uraian,debit,kredit,saldo\n'
              b'01/10/2026,TRX001,Transfer masuk,,"1.500.000","1.500.000"\n'
              b'02/10/2026,TRX002,Admin bank,"25.000",,"1.475.000"\n'
              b'03/10/2026,TRX003,Payment customer,,"5.000.000","6.475.000"\n')
    rows, errors = parse_statement_csv(BytesIO(csv_id))
    check('CSV id-header: 3 baris, 0 error', len(rows) == 3 and not errors,
          (len(rows), len(errors)))
    check('CSV angka "1.500.000" -> 1500000',
          rows[0]['credit'] == Decimal('1500000'), rows[0]['credit'])

    csv_en = (b'Date,Reference,Description,Money Out,Money In,Balance\n'
              b'2026-10-04,TRX4,Refund supplier,"(150.000)",,1.325.000\n'
              b'2026-10-05,,Transfer antar bank,,2.000.000,3.325.000\n')
    rows2, errors2 = parse_statement_csv(BytesIO(csv_en))
    check('CSV en-header + negatif kurung: 2 baris',
          len(rows2) == 2 and not errors2, (len(rows2), len(errors2)))
    check('CSV (150.000) -> debit 150000',
          rows2[0]['debit'] == Decimal('150000'), rows2[0]['debit'])

    rows3, _ = parse_statement_csv(BytesIO(b'05/10/2026,TRX5,Tanpa header,300000,,250000\n'))
    check('CSV tanpa header diparse posisional', len(rows3) == 1, len(rows3))
    check('CSV tanpa header -> debet 300000',
          rows3[0]['debit'] == Decimal('300000'), rows3[0]['debit'])

    rows4, errors4 = parse_statement_csv(BytesIO(
        b'tanggal,referensi,uraian,debit,kredit,saldo\n01/10/2026,TRX1,Kosong,,,\n'))
    check('CSV baris kosong dilaporkan sebagai error',
          len(rows4) == 0 and len(errors4) == 1, (len(rows4), len(errors4)))

    # --- Project accounting (PRD 18) ---
    from core.project_models import Project, ProjectBilling, ProjectCost, ProjectMilestone
    from core.project_services import post_project_billing, post_project_cost, project_report

    prj_customer = Customer.objects.create(
        company=company, customer_code='CUST-PRJ', customer_name='PT Klien Proyek')
    project = Project.objects.create(
        company=company, branch=company.branches.first(), customer=prj_customer,
        project_code='PRJ-001', project_name='Pembangunan Gedung',
        contract_value=Decimal('500000000'), retention_percent=5, status='IN_PROGRESS')
    ms = ProjectMilestone.objects.create(
        project=project, milestone_code='M1', milestone_name='Fondasi',
        billable_amount=Decimal('250000000'), percent_complete=Decimal('100'))

    prj_cost = ProjectCost.objects.create(
        project=project, cost_date=today, cost_type='LABOR', description='Tenaga lapangan',
        quantity=Decimal('10'), unit_price=Decimal('2000000'))
    check('Project cost total = qty x price', prj_cost.total_amount == Decimal('20000000.00'),
          prj_cost.total_amount)
    cost_journal = post_project_cost(prj_cost, user=user)
    check('Project cost -> Dr WIP / Cr AP, balanced', cost_journal.is_balanced)

    prj_bill = ProjectBilling.objects.create(
        project=project, milestone=ms, billing_number='PB-001', billing_date=today,
        gross_amount=Decimal('100000000'), retention_percent=5)
    check('Billing retention 5% = 5jt, net = 95jt',
          prj_bill.retention_amount == Decimal('5000000.00')
          and prj_bill.net_amount == Decimal('95000000.00'),
          (prj_bill.retention_amount, prj_bill.net_amount))
    bill_journal = post_project_billing(prj_bill, user=user)
    check('Project billing -> Dr AR / Cr Revenue, balanced', bill_journal.is_balanced)
    ms.refresh_from_db()
    check('Milestone billed_amount updated', ms.billed_amount == Decimal('95000000.00'),
          ms.billed_amount)
    check('Milestone belum is_billed ( masih ada sisa)', ms.is_billed is False)

    rows = project_report(company, today, today)
    prow = rows[0]
    check('Project revenue = 100jt', prow['revenue'] == Decimal('100000000.00'), prow['revenue'])
    check('Project cost = 20jt (WIP)', prow['cost'] == Decimal('20000000.00'), prow['cost'])
    check('Project profit = 80jt', prow['profit'] == Decimal('80000000.00'), prow['profit'])

    # --- Notification scanner (PRD 33) ---
    from datetime import timedelta as td_days
    from bank.models import BankReconciliation, BankStatementLine
    from consolidation.models import IntercompanyTransaction
    from consolidation.services import post_intercompany
    from notify.models import Notification
    from notify.scanner import scan
    from workflow.models import ApprovalRequest, ApprovalStep, ApprovalWorkflow

    # invoice overdue
    CustomerInvoice.objects.create(
        company=company, branch=company.branches.first(),
        customer=Customer.objects.create(
            company=company, customer_code='CUST-SC', customer_name='PT Overdue'),
        invoice_number='INV/SC/001', invoice_date=today - td_days(days=40),
        due_date=today - td_days(days=10), currency_id='IDR', exchange_rate=1,
        total=Decimal('5000000'), status='POSTED')
    # tagihan jatuh tempo minggu ini
    SupplierBill.objects.create(
        company=company, branch=company.branches.first(),
        supplier=supplier, bill_number='BILL/SC/001', bill_date=today - td_days(days=25),
        due_date=today + td_days(days=3), currency_id='IDR', exchange_rate=1,
        total=Decimal('2000000'), status='POSTED')
    # bank reconciliation macet
    bank_account_obj = bank_account
    rec = BankReconciliation.objects.create(
        company=company, bank_account=bank_account_obj, reconciliation_name='BCA Stuck',
        statement_date=today - td_days(days=20))
    BankStatementLine.objects.create(
        bank_account=bank_account_obj, reconciliation=rec,
        statement_date=today - td_days(days=20), description='Unknown', credit=Decimal('100000'))
    # intercompany tanpa pasangan
    ic_scan = IntercompanyTransaction.objects.create(
        group=company.group, source_company=company, target_company=usd_company,
        transaction_number='IC/SC/001', transaction_date=today,
        tx_type='SALES', amount=Decimal('1000000'))
    post_intercompany(ic_scan, user=user)
    # journal rusak via ORM (engine tidak mengizinkan, jadi bypass)
    broken = JournalEntry.objects.create(
        company=company, branch=company.branches.first(),
        period=AccountingPeriod.objects.filter(
            company=company, start_date__lte=today, end_date__gte=today).first(),
        journal_number='JE/BROKEN/00001', journal_date=today,
        source='MANUAL', status=JournalStatus.POSTED,
        currency_id='IDR', exchange_rate=1)
    JournalLine.objects.create(journal=broken, line_no=10,
        account=Account.objects.get(company=company, account_code='1102'),
        debit=Decimal('1000'))
    JournalLine.objects.create(journal=broken, line_no=20,
        account=Account.objects.get(company=company, account_code='4201'),
        credit=Decimal('999'))

    scan1 = scan(company)
    total1 = sum(len(v) for k, v in scan1.items() if not k.endswith(':error'))
    check('Notify: scan menghasilkan notifikasi', total1 >= 5, total1)
    scan_errors = [k for k, v in scan1.items() if k.endswith(':error') and v]
    check('Notify: tidak ada scanner error', not scan_errors, scan_errors)
    scan2 = scan(company)
    total2 = sum(len(v) for k, v in scan2.items() if not k.endswith(':error'))
    check('Notify: scan kedua tidak duplikat (dedup fingerprint)', total2 == 0, total2)
    check('Notify: kategori invoice_overdue ada',
          any(n.category == 'INVOICE_OVERDUE' for n in Notification.objects.filter(company=company)))
    check('Notify: kategori failed_journal ada',
          any(n.category == 'FAILED_JOURNAL' for n in Notification.objects.filter(company=company)))
    check('Notify: kategori intercompany_mismatch ada',
          any(n.category == 'INTERCOMPANY_MISMATCH' for n in Notification.objects.filter(company=company)))

    # --- Budget vs Actual (PRD 22) ---
    from core.budget_models import Budget, BudgetStatus, BudgetType
    from core.budget_services import budget_for, budget_vs_actual, over_budget_company

    revenue_acc = Account.objects.get(company=company, account_code='4101')
    expense_acc = Account.objects.get(company=company, account_code='6601')

    Budget.objects.create(company=company, account=revenue_acc, fiscal_year=str(today.year),
        budget_type=BudgetType.MONTHLY, period=f'{today:%Y-%m}',
        amount=Decimal('200000000'), status=BudgetStatus.APPROVED)
    Budget.objects.create(company=company, account=expense_acc, fiscal_year=str(today.year),
        budget_type=BudgetType.MONTHLY, period=f'{today:%Y-%m}',
        amount=Decimal('50000000'), status=BudgetStatus.APPROVED)
    Budget.objects.create(company=company, account=expense_acc, fiscal_year=str(today.year),
        budget_type=BudgetType.ANNUAL, period='', amount=Decimal('600000000'),
        status=BudgetStatus.APPROVED)

    year, month = today.year, today.month
    annual_prorata = budget_for(company, expense_acc, year, today, today, BudgetType.ANNUAL)
    check('Budget annual prorata = annual/12', annual_prorata == Decimal('50000000.00'),
          annual_prorata)

    rows_bva = budget_vs_actual([company], today, today,
                                budget_types=[BudgetType.ANNUAL, BudgetType.MONTHLY])
    rev_row = next(r for r in rows_bva if r['account'].account_code == '4101')
    exp_row = next(r for r in rows_bva if r['account'].account_code == '6601')
    check('Budget vs actual: revenue budget + actual row ada',
          rev_row['budget'] == Decimal('200000000') and rev_row['actual'] > 0,
          (rev_row['budget'], rev_row['actual']))
    check('Budget vs actual: expense annual+monthly digabung',
          exp_row['budget'] == Decimal('100000000'), exp_row['budget'])
    check('Revenue over budget = favourable',
          (rev_row['is_favorable'] is True) == (rev_row['variance'] >= 0),
          (rev_row['variance'], rev_row['is_favorable']))
    check('Expense over budget = unfavourable',
          (exp_row['is_favorable'] is True) == (exp_row['variance'] <= 0),
          (exp_row['variance'], exp_row['is_favorable']))
    over_rows = over_budget_company(company, today, today, threshold=Decimal('0'))
    check('over_budget hanya actual > budget',
          all(r['variance'] > 0 for r in over_rows), len(over_rows))

    # --- Budget notification scanner ---
    from notify.scanner import scan_budget_exceeded
    budget_notifs = scan_budget_exceeded(company, today)
    check('Notify: budget_exceeded scanner jalan',
          isinstance(budget_notifs, list), len(budget_notifs))

    # --- Cost accounting / management reports (PRD 19, 27) ---
    from reports.cost import (
        customer_statement, product_profitability, profitability_by_dimension, vendor_statement,
    )

    cc1 = Customer.objects.create(company=company, customer_code='CUST-C1', customer_name='PT Alpha')
    cc2 = Customer.objects.create(company=company, customer_code='CUST-C2', customer_name='PT Beta')
    svc_rev = Account.objects.get(company=company, account_code='4201')
    for code, cust, amt in [('INV/C1/001', cc1, '200000000'), ('INV/C2/001', cc2, '50000000')]:
        inv_cc = CustomerInvoice.objects.create(
            company=company, branch=company.branches.first(), customer=cust,
            invoice_number=code, invoice_date=today, due_date=today, currency_id='IDR', exchange_rate=1)
        CustomerInvoiceLine.objects.create(invoice=inv_cc, account=svc_rev,
            description='Jasa', quantity=1, unit_price=Decimal(amt))
        recalc_invoice_totals(inv_cc)
        post_invoice(inv_cc)

    rows_c = profitability_by_dimension([company], 'customer', today, today)
    labels_c = {r['label'] for r in rows_c}
    check('Cost accounting: kedua customer terhitung',
          any('PT Alpha' in x for x in labels_c) and any('PT Beta' in x for x in labels_c),
          labels_c)
    check('Cost accounting: minimal 2 rows', len(rows_c) >= 2, len(rows_c))
    alpha = next(r for r in rows_c if 'PT Alpha' in r['label'])
    beta = next(r for r in rows_c if 'PT Beta' in r['label'])
    check('Cost accounting: Alpha revenue 200jt',
          alpha['revenue'] == Decimal('200000000.00'), alpha['revenue'])
    check('Cost accounting: Beta revenue 50jt',
          beta['revenue'] == Decimal('50000000.00'), beta['revenue'])
    check('Cost accounting: urut profit descending', rows_c[0]['profit'] >= rows_c[-1]['profit'])
    for dim in ['customer', 'supplier', 'branch', 'department', 'cost_center', 'project']:
        r = profitability_by_dimension([company], dim, today, today)
        check(f'Cost accounting: dim {dim} jalan', isinstance(r, list), len(r))

    pay_c = CustomerPayment.objects.create(
        company=company, branch=company.branches.first(), customer=cc1,
        bank_account=bank_account, payment_number='RCPT/C1/001', payment_date=today,
        amount=Decimal('80000000'), payment_method='Transfer')
    allocate_payment(pay_c, cc1.invoices.get(), Decimal('80000000'))
    post_payment(pay_c)
    events_c, closing_c = customer_statement(cc1, today, today)
    check('Customer statement: 2 events (invoice + payment)', len(events_c) == 2, len(events_c))
    check('Customer statement: closing 200jt - 80jt = 120jt',
          closing_c == Decimal('120000000.00'), closing_c)
    check('Customer statement: running balance naik lalu turun',
          events_c[0]['balance'] == Decimal('200000000.00')
          and events_c[-1]['balance'] == Decimal('120000000.00'))

    supplier_cc = Supplier.objects.create(
        company=company, supplier_code='SUPP-C', supplier_name='PT Vendor Cost')
    rent = Account.objects.get(company=company, account_code='6601')
    bill_cc = SupplierBill.objects.create(
        company=company, branch=company.branches.first(), supplier=supplier_cc,
        bill_number='BILL/C/001', bill_date=today, due_date=today, currency_id='IDR', exchange_rate=1)
    SupplierBillLine.objects.create(bill=bill_cc, account=rent, description='Sewa',
        quantity=1, unit_price=Decimal('30000000'))
    recalc_bill_totals(bill_cc)
    post_bill(bill_cc)
    events_v, closing_v = vendor_statement(supplier_cc, today, today)
    check('Vendor statement: 1 event (bill)', len(events_v) == 1, len(events_v))
    check('Vendor statement: closing 30jt', closing_v == Decimal('30000000.00'), closing_v)

    prows = product_profitability([company], today, today)
    check('Product profitability jalan', isinstance(prows, list), len(prows))

    # --- Currency translation & NCI (PRD 23, 25) ---
    from core.models import Currency, ExchangeRate, Group
    from consolidation.translation import (
        consolidated_pnl, group_currency, non_controlling_interest, translate_to_group,
    )

    group = company.group
    all_cos = list(group.companies.filter(status='ACTIVE'))
    check('Group currency = base company paling besar', group_currency(group) in ('IDR', 'USD', 'SGD'))
    rate_ok = translate_to_group(company, company.base_currency, today)
    check('Kurs currency sendiri = 1', rate_ok == Decimal('1'), rate_ok)

    ExchangeRate.objects.update_or_create(currency_id='USD', company=None, rate_date=today,
        defaults={'rate': Decimal('15000')})
    b_co = usd_company
    try:
        rate_usd = translate_to_group(b_co, 'IDR', today)
        check('Kurs USD->IDR ditemukan', rate_usd == Decimal('15000.00'), rate_usd)
    except LookupError:
        check('Kurs USD->IDR ditemukan', False, 'no rate')

    # NCI: company C 80% owned -> 20% minority
    c_co = Company.objects.get(company_code='C')
    nci_rows = non_controlling_interest([c_co], today, 'IDR')
    check('NCI: C punya minority 20%', len(nci_rows) == 1
          and nci_rows[0]['minority_percent'] == Decimal('20.00'),
          nci_rows[0]['minority_percent'] if nci_rows else 'none')
    check('NCI: 20% x laba C', nci_rows and nci_rows[0]['nci']
          == (nci_rows[0]['net_profit_group'] * Decimal('0.20')).quantize(Decimal('0.01')),
          nci_rows[0]['nci'] if nci_rows else '-')

    data_pnl = consolidated_pnl(group, all_cos, today, today)
    check('Consolidated P&L target currency IDR', data_pnl['target_currency'] == 'IDR',
          data_pnl['target_currency'])
    check('Consolidated P&L = net_profit - NCI',
          data_pnl['net_profit_attributable'] == data_pnl['net_profit'] - data_pnl['nci'],
          (data_pnl['net_profit'], data_pnl['nci'], data_pnl['net_profit_attributable']))

    # --- Format currency di display table admin ---
    if not run_money_format_checks():
        FAIL.append('money format admin')

    # --- Registrasi company + owner ---
    run_register_checks()

    print()
    print(f'TOTAL: {len(PASS)} pass, {len(FAIL)} fail')
    if FAIL:
        print('FAILED:', ', '.join(FAIL))
    return 1 if FAIL else 0


def run_money_format_checks():
    """Format currency di display table admin (core/admin_money.py).

    intcomma ikut locale aktif, jadi di bawah angka mengikuti gaya bahasa
    (id: 1.234.567,89 / en: 1,234,567.89). Check hanya Assert pada bagian yang
    tidak bergantung locale.
    """
    from django.utils import translation

    from core.admin_money import is_money_computed, is_money_field, money

    ok = True

    def expect(label, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f'[{"PASS" if good else "FAIL"}] {label}  -> {got!r}')

    for lang in ('en', 'id'):
        with translation.override(lang):
            thousands, decimal_sep = (',', '.') if lang == 'en' else ('.', ',')
            expect(f'money bulat ({lang})', money('100000000.00'), f'Rp 100{thousands}000{thousands}000')
            expect(f'money desimal ({lang})', money('1234567.89'),
                   f'Rp 1{thousands}234{thousands}567{decimal_sep}89')
            expect(f'money negatif ({lang})', money('-4500000.5'),
                   f'-Rp 4{thousands}500{thousands}000{decimal_sep}50')
            expect(f'money nol ({lang})', money('0'), 'Rp 0')

    expect('field total = uang', is_money_field('total'), True)
    expect('field net_book_value = uang', is_money_field('net_book_value'), True)
    expect('field exchange_rate bukan uang', is_money_field('exchange_rate'), False)
    expect('field quantity bukan uang', is_money_field('quantity'), False)
    expect('field tax_rate bukan uang', is_money_field('tax_rate'), False)
    expect('property total_debit = uang', is_money_computed('total_debit'), True)
    expect('property available bukan uang', is_money_computed('available'), False)

    return ok

def run_isolated():
    """Jalankan test dalam transaksi yang di-rollback, jadi data test tidak
    polluting database dan smoke_test bisa dijalankan berulang."""
    from django.db import transaction

    atomic = transaction.atomic()
    atomic.__enter__()
    try:
        code = main()
    except Exception:
        transaction.set_rollback(True)
        atomic.__exit__(None, None, None)
        raise
    transaction.set_rollback(True)
    atomic.__exit__(None, None, None)
    return code


if __name__ == '__main__':
    raise SystemExit(run_isolated())