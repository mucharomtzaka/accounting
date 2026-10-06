from datetime import date
from decimal import Decimal

from django.db.models import F, Sum
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from core.models import Company
from gl.models import JournalEntry, JournalStatus
from . import engine


def _date_param(request, name, default):
    """Baca parameter tanggal dari query string sebagai `date`.

    Tanpa ini `request.GET.get('end')` mengembalikan str, dan service yang
    memanggil `end.year` (budget_vs_actual) jadi 500 begitu user memilih
    rentang tanggal di form filter. Input tidak valid / kosong jatuh ke default
    supaya halaman tetap terbuka.
    """
    raw = request.GET.get(name)
    if not raw:
        return default
    return parse_date(raw) or default


def _pk_param(request, name):
    """PK dari query string sebagai int, atau None kalau kosong / bukan angka.

    Tanpa ini `filter(pk='abc')` dari form filter melempar ValueError dan
    halaman jadi 500.
    """
    try:
        return int(request.GET.get(name) or '')
    except ValueError:
        return None


def _scope(request):
    scoped = request.user.allowed_companies(request)
    all_companies = request.user.allowed_companies()
    cid = _pk_param(request, 'company')
    companies = scoped.filter(pk=cid) if cid else scoped
    return all_companies, companies


def dashboard(request):
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    # Periode default: awal tahun s/d hari ini, bisa diubah lewat filter. `today`
    # di-override ke tanggal akhir supaya semua perhitungan di bawah (P&L, kas,
    # aging, overdue, tren) ikut periode yang dipilih, bukan selalu hari ini.
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    if start > end:
        start, end = end, start
    today = end

    data = {'all_companies': all_companies, 'today': today, 'start': start, 'end': end,
            'companies': companies}
    if companies.exists():
        from ap.models import SupplierBill
        from ar.models import CustomerInvoice

        data['pnl'] = engine.profit_and_loss(companies, start, end)
        data['balance_sheet'] = engine.balance_sheet(companies, end)
        data['cash_flow'] = engine.cash_flow(companies, start, end)
        trend = engine.monthly_trend(companies, today, months=12)
        data['trend'] = trend
        data['trend_max'] = max(
            [abs(t['revenue']) for t in trend] + [abs(t['expense']) for t in trend] + [1]
        )

        open_documents = CustomerInvoice.objects.filter(company__in=companies).exclude(
            amount_paid__gte=F('total')
        )
        data['ar_total'] = open_documents.aggregate(t=Sum('total'), p=Sum('amount_paid'))
        data['ar_overdue_count'] = open_documents.filter(due_date__lt=today).count()

        open_bills = SupplierBill.objects.filter(company__in=companies).exclude(
            amount_paid__gte=F('total')
        )
        data['ap_total'] = open_bills.aggregate(t=Sum('total'), p=Sum('amount_paid'))
        data['ap_overdue_count'] = open_bills.filter(due_date__lt=today).count()

        data['draft_invoices'] = CustomerInvoice.objects.filter(
            company__in=companies, status='DRAFT').count()
        data['draft_bills'] = SupplierBill.objects.filter(company__in=companies, status='DRAFT').count()
        data['draft_journals'] = JournalEntry.objects.filter(
            company__in=companies, status=JournalStatus.DRAFT).count()

    data['ar_rows'] = [
        (c, _aging_view(engine.ar_aging(c, today))) for c in companies
    ]

    if companies.exists():
        expense_lines = data['pnl']['expense_lines']
        top = sorted(expense_lines, key=lambda r: r['amount'], reverse=True)[:6]
        expense_max = max([r['amount'] for r in top] + [1])
        data['expense_top'] = [
            {
                'label': r['account'].account_name,
                'company': r['company'].company_code,
                'amount': r['amount'],
                # Persen dipakai template untuk lebar bar, dikirim sudah dihitung.
                'pct': round(r['amount'] / expense_max * 100),
            }
            for r in top
        ]
        merged = _merge_aging(engine.ar_aging(c, today) for c in companies)
        total = sum(merged.values()) or Decimal('1')
        data['aging_total'] = [
            ('Current', merged['Current'], round(merged['Current'] / total * 100)),
            ('1-30 hari', merged['1-30'], round(merged['1-30'] / total * 100)),
            ('31-60 hari', merged['31-60'], round(merged['31-60'] / total * 100)),
            ('61-90 hari', merged['61-90'], round(merged['61-90'] / total * 100)),
            ('>90 hari', merged['>90'], round(merged['>90'] / total * 100)),
        ]
    return render(request, 'reports/dashboard.html', data)


def _merge_aging(bucket_iter):
    """Gabung bucket aging dari banyak company."""
    merged = {'Current': Decimal('0'), '1-30': Decimal('0'), '31-60': Decimal('0'),
              '61-90': Decimal('0'), '>90': Decimal('0')}
    for buckets in bucket_iter:
        for key in merged:
            merged[key] += buckets[key]
    return merged


def _aging_view(buckets):
    """Template tidak bisa akses key '>90', jadi di-map ke nama valid."""
    return {
        'current': buckets['Current'], 'd30': buckets['1-30'], 'd60': buckets['31-60'],
        'd90': buckets['61-90'], 'over90': buckets['>90'],
        'total': sum(buckets.values()),
    }


def trial_balance_report(request):
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    rows, total_debit, total_credit = engine.trial_balance(companies, start, end)
    return render(request, 'reports/trial_balance.html', {
        'all_companies': all_companies, 'companies': companies, 'rows': rows,
        'total_debit': total_debit, 'total_credit': total_credit,
        'is_balanced': total_debit == total_credit, 'start': start, 'end': end,
    })


def profit_loss_report(request):
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    data = engine.profit_and_loss(companies, start, end) if companies.exists() else {}
    return render(request, 'reports/profit_loss.html', {
        'all_companies': all_companies, 'companies': companies, 'data': data, 'start': start, 'end': end,
    })


def balance_sheet_report(request):
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    as_of = _date_param(request, 'as_of', today)
    data = engine.balance_sheet(companies, as_of) if companies.exists() else {}
    return render(request, 'reports/balance_sheet.html', {
        'all_companies': all_companies, 'companies': companies, 'data': data, 'as_of': as_of,
    })


def cash_flow_report(request):
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    data = engine.cash_flow(companies, start, end) if companies.exists() else {}
    return render(request, 'reports/cash_flow.html', {
        'all_companies': all_companies, 'companies': companies, 'data': data, 'start': start, 'end': end,
    })


def general_ledger_report(request):
    all_companies, companies = _scope(request)
    from gl.models import Account

    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    if start > end:
        start, end = end, start
    accounts = Account.objects.filter(company__in=companies, is_postable=True, is_active=True)
    acc_pk = _pk_param(request, 'account')
    account = accounts.filter(pk=acc_pk).first() if acc_pk else None

    rows, opening = [], Decimal('0')
    if account:
        rows, opening = engine.general_ledger(companies, account, start, end)

    return render(request, 'reports/general_ledger.html', {
        'all_companies': all_companies, 'companies': companies, 'accounts': accounts,
        'account': account, 'rows': rows, 'opening': opening, 'start': start, 'end': end,
    })


def balance_comparison_report(request):
    """Neraca saldo perbandingan: saldo per `start` vs saldo per `end`."""
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    end = _date_param(request, 'end', today)
    # Default pembanding: akhir tahun sebelumnya.
    start = _date_param(request, 'start', date(end.year - 1, 12, 31))
    if start > end:
        start, end = end, start
    rows, totals = ([], {})
    if companies.exists():
        rows, totals = engine.balance_comparison(companies, start, end)
    return render(request, 'reports/balance_comparison.html', {
        'all_companies': all_companies, 'companies': companies,
        'rows': rows, 'totals': totals, 'start': start, 'end': end,
    })


def aging_report(request):
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    return render(request, 'reports/aging.html', {
        'all_companies': all_companies, 'companies': companies,
        'ar_rows': [(c, _aging_view(engine.ar_aging(c, today))) for c in companies],
        'ap_rows': [(c, _aging_view(engine.ap_aging(c, today))) for c in companies],
        'as_of': today,
    })


def company_comparison(request):
    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    from consolidation.translation import group_currency, translate_to_group
    from core.models import Group

    groups = Group.objects.filter(companies__in=companies).distinct()
    gc = None
    if groups.exists():
        gc = group_currency(groups.first())

    rows = []
    for c in companies:
        pnl = engine.profit_and_loss([c], start, end)
        rate = Decimal('1')
        rate_note = 'sama dengan group currency'
        try:
            rate = translate_to_group(c, gc, end)
            rate_note = f'x{rate}'
        except LookupError:
            rate = Decimal('1')
            rate_note = 'TIDAK ADA KURS - tidak ditranslasi'
        row = {'company': c, 'rate': rate, 'rate_note': rate_note, **pnl}
        for key in ('revenue', 'cogs', 'gross_profit', 'expense', 'net_profit'):
            row[key] = (pnl[key] * rate).quantize(Decimal('0.01'))
        rows.append(row)

    totals = {
        key: sum((r[key] for r in rows), Decimal('0'))
        for key in ('revenue', 'cogs', 'gross_profit', 'expense', 'net_profit')
    }
    return render(request, 'reports/company_comparison.html', {
        'all_companies': all_companies, 'companies': companies, 'rows': rows,
        'totals': totals, 'start': start, 'end': end,
        'group_currency': gc,
    })


def consolidated_report(request):
    """PRD 25 - Consolidated P&L dengan currency translation + NCI + IC exception."""
    from consolidation.translation import consolidated_pnl
    from core.models import Group

    user = request.user
    groups = Group.objects.filter(companies__in=user.allowed_companies(request)) if not user.is_superuser \
        else Group.objects.all()
    gid = request.GET.get('group')
    group = groups.filter(pk=gid).first() if gid else (groups.first() if groups.exists() else None)

    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    data = None
    if group:
        companies = group.companies.filter(status='ACTIVE')
        data = consolidated_pnl(group, companies, start, end)

    return render(request, 'reports/consolidated.html', {
        'groups': groups, 'group': group, 'data': data,
        'start': start, 'end': end,
    })


def project_profitability_report(request):
    from core.models import Project
    from core.project_services import project_report

    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    rows = []
    for company in companies:
        rows.extend(project_report(company, start, end))

    totals = {
        'revenue': sum(r['revenue'] for r in rows),
        'cost': sum(r['cost'] for r in rows),
        'profit': sum(r['profit'] for r in rows),
    }
    return render(request, 'reports/project_profitability.html', {
        'all_companies': all_companies, 'companies': companies, 'rows': rows,
        'totals': totals, 'start': start, 'end': end,
    })


def budget_vs_actual_report(request):
    from core.budget_models import BudgetType
    from core.budget_services import budget_vs_actual

    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    btype = request.GET.get('btype') or BudgetType.MONTHLY
    if btype not in (BudgetType.ANNUAL, BudgetType.MONTHLY):
        btype = BudgetType.MONTHLY

    # budget monthly + annual prorata digabung supaya tak dobel saat keduanya terisi
    rows = budget_vs_actual(companies, start, end, budget_type=btype,
                            budget_types=[BudgetType.ANNUAL, BudgetType.MONTHLY])
    totals = {
        'budget': sum(r['budget'] for r in rows),
        'actual': sum(r['actual'] for r in rows),
        'variance': sum(r['variance'] for r in rows),
        'variance_percent': (
            (sum(r['actual'] for r in rows) - sum(r['budget'] for r in rows))
            / sum(r['budget'] for r in rows) * 100
            if sum(r['budget'] for r in rows) else 0
        ),
    }
    return render(request, 'reports/budget_vs_actual.html', {
        'all_companies': all_companies, 'companies': companies, 'rows': rows,
        'totals': totals, 'start': start, 'end': end, 'btype': btype,
    })


def cost_accounting_report(request):
    from reports.cost import DIMENSIONS, profitability_by_dimension

    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    dimension = request.GET.get('dimension') or 'customer'
    if dimension not in DIMENSIONS:
        dimension = 'customer'

    rows = profitability_by_dimension(companies, dimension, start, end)
    totals = {
        'revenue': sum(r['revenue'] for r in rows),
        'cogs': sum(r['cogs'] for r in rows),
        'expense': sum(r['expense'] for r in rows),
        'profit': sum(r['profit'] for r in rows),
    }
    return render(request, 'reports/cost_accounting.html', {
        'all_companies': all_companies, 'companies': companies, 'rows': rows,
        'totals': totals, 'start': start, 'end': end,
        'dimension': dimension, 'dimensions': DIMENSIONS,
    })


def product_profitability_report(request):
    from reports.cost import product_profitability

    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    rows = product_profitability(companies, start, end)
    totals = {
        'revenue': sum(r['revenue'] for r in rows),
        'cogs': sum(r['cogs'] for r in rows),
        'profit': sum(r['profit'] for r in rows),
    }
    return render(request, 'reports/product_profitability.html', {
        'all_companies': all_companies, 'companies': companies, 'rows': rows,
        'totals': totals, 'start': start, 'end': end,
    })


def customer_statement_report(request):
    from ar.models import Customer
    from reports.cost import customer_statement

    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    customer = None
    events = []
    closing_balance = Decimal('0')

    cid = request.GET.get('customer')
    if cid:
        customer = Customer.objects.filter(pk=cid, company__in=companies).first()
        if customer:
            events, closing_balance = customer_statement(customer, start, end)

    return render(request, 'reports/customer_statement.html', {
        'all_companies': all_companies, 'companies': companies,
        'customers': Customer.objects.filter(company__in=companies),
        'customer': customer, 'events': events, 'closing_balance': closing_balance,
        'start': start, 'end': end,
    })


def vendor_statement_report(request):
    from ap.models import Supplier
    from reports.cost import vendor_statement

    all_companies, companies = _scope(request)
    today = timezone.localdate()
    start = _date_param(request, 'start', today.replace(month=1, day=1))
    end = _date_param(request, 'end', today)
    supplier = None
    events = []
    closing_balance = Decimal('0')

    sid = request.GET.get('supplier')
    if sid:
        supplier = Supplier.objects.filter(pk=sid, company__in=companies).first()
        if supplier:
            events, closing_balance = vendor_statement(supplier, start, end)

    return render(request, 'reports/vendor_statement.html', {
        'all_companies': all_companies, 'companies': companies,
        'suppliers': Supplier.objects.filter(company__in=companies),
        'supplier': supplier, 'events': events, 'closing_balance': closing_balance,
        'start': start, 'end': end,
    })
