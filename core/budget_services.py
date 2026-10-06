"""PRD 22 - Budget vs actual.

Actual diambil dari General Ledger (journal POSTED), bukan dokumen sumber,
supaya selalu konsisten dengan laporan lain. Variance dihitung terhadap
akun & dimensi yang sama, dengan budget annual dibagi rata ke 12 bulan
supaya bisa dibandingkan dengan budget bulanan.
"""
from decimal import Decimal

from django.db.models import Sum

from gl.models import Account, AccountCategory, JournalLine, JournalStatus

from .budget_models import Budget, BudgetStatus, BudgetType

POSTED = [JournalStatus.POSTED, JournalStatus.RECONCILED]


def actual_amount(company, account, start, end, branch=None, department=None,
                  cost_center=None, project=None):
    """Saldo actual satu akun untuk satu company dalam periode tertentu."""
    qs = JournalLine.objects.filter(
        journal__company=company,
        journal__status__in=POSTED,
        journal__journal_date__gte=start,
        journal__journal_date__lte=end,
        account=account,
    )
    if branch:
        qs = qs.filter(journal__branch=branch)
    if department:
        qs = qs.filter(journal__department=department)
    if cost_center:
        qs = qs.filter(journal__cost_center=cost_center)
    if project:
        qs = qs.filter(journal__project=project)

    debit = qs.aggregate(d=Sum('debit'))['d'] or Decimal('0')
    credit = qs.aggregate(c=Sum('credit'))['c'] or Decimal('0')
    balance = debit - credit
    # revenue credit-normal -> positif; expense debit-normal -> positif
    if account.account_category in (AccountCategory.REVENUE, AccountCategory.EXPENSE,
                                    AccountCategory.COGS):
        return abs(balance)
    return balance


def _months_in_period(start, end):
    months = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        months.append(f'{year}-{month:02d}')
        month += 1
        if month > 12:
            month = 1
            year += 1
    return months


def budget_for(company, account, fiscal_year, start, end, budget_type=BudgetType.MONTHLY,
               branch=None, department=None, cost_center=None, project=None):
    """Total budget applicable untuk satu akun + satu tipe budget dalam periode.

    MONTHLY: jumlah baris bulanan yang jatuh dalam periode.
    ANNUAL : total annual dibagi rata ke 12, dikali jumlah bulan dalam periode.

    Tipe bisa digabung oleh pemanggil dengan menjumlahkan keduanya
    (mis. ANNUAL prorata + MONTHLY).
    """
    period_months = _months_in_period(start, end)

    qs = Budget.objects.filter(
        company=company, account=account, fiscal_year=str(fiscal_year),
    )
    for field, value in (('branch', branch), ('department', department),
                         ('cost_center', cost_center), ('project', project)):
        qs = qs.filter(**{f'{field}_id': getattr(value, 'id', None)})

    if budget_type == BudgetType.ANNUAL:
        annual = qs.filter(budget_type=BudgetType.ANNUAL, period='').aggregate(
            t=Sum('amount'))['t'] or Decimal('0')
        return (annual / Decimal(12)) * Decimal(len(period_months))

    monthly = qs.filter(
        budget_type=BudgetType.MONTHLY, period__in=period_months
    ).aggregate(t=Sum('amount'))['t'] or Decimal('0')
    return monthly


def variance_percent(budget, actual):
    if budget == 0:
        return Decimal('0')
    return ((actual - budget) / budget * Decimal('100')).quantize(Decimal('0.01'))


def budget_vs_actual(companies, start, end, budget_type=BudgetType.MONTHLY,
                     include_zero_actual=False, budget_types=None):
    """PRD 22 - Budget / Actual / Variance / Variance %.

    budget_type: tipe utama yang ditampilkan.
    budget_types: kalau diisi, tipe-tipe ini dijumlahkan (mis. ANNUAL + MONTHLY).
    Sesuai account + dimensi, actual diambil dari GL.

    Return list of rows per company + akun + dimensi.
    """
    rows = []
    types = budget_types or [budget_type]

    for company in companies:
        budgets = Budget.objects.filter(
            company=company,
            fiscal_year=str(end.year),
            status__in=[BudgetStatus.APPROVED, BudgetStatus.SUBMITTED, BudgetStatus.DRAFT],
        ).select_related('account', 'branch', 'department')

        # key = (account, branch, department) -> track unique rows
        seen = {}
        for budget in budgets:
            key = (budget.account_id, budget.branch_id, budget.department_id)
            seen.setdefault(key, budget)

        for (account_id, branch_id, dept_id), budget in seen.items():
            account = Account.objects.filter(pk=account_id).first()
            if not account:
                continue
            budget_amount = sum(
                (budget_for(company, account, end.year, start, end, t,
                            branch=budget.branch, department=budget.department)
                 for t in types), Decimal('0')
            )
            actual = actual_amount(
                company, account, start, end,
                branch=budget.branch, department=budget.department,
            )
            if actual == 0 and budget_amount == 0 and not include_zero_actual:
                continue
            rows.append(_row(company, budget, actual, budget_amount=budget_amount))

        # akun yang ada actual tapi belum ada budget-nya
        actual_accounts = (
            JournalLine.objects.filter(
                journal__company=company, journal__status__in=POSTED,
                journal__journal_date__gte=start, journal__journal_date__lte=end,
                account__account_category__in=[
                    AccountCategory.REVENUE, AccountCategory.EXPENSE, AccountCategory.COGS],
            )
            .exclude(account_id__in={k[0] for k in seen})
            .values_list('account_id', flat=True).distinct()
        )
        for account_id in actual_accounts:
            account = Account.objects.filter(pk=account_id).first()
            if not account:
                continue
            actual = actual_amount(company, account, start, end)
            if actual == 0:
                continue
            rows.append(_row(company, None, actual, account=account, budget_amount=Decimal('0')))

    return rows


def _row(company, budget, actual, account=None, budget_amount=None):
    account = account or budget.account
    if budget is None:
        branch = department = None
        period_label = '-'
    else:
        branch = budget.branch
        department = budget.department
        period_label = budget.period or 'Annual'
    if budget_amount is None:
        budget_amount = budget.amount

    variance = actual - budget_amount
    # revenue: actual > budget = bagus (pencapaian/decah). expense/cogs: actual <
    # budget = bagus (belum spending). Tanda variance tidak selalu "baik".
    is_revenue = account.account_category == AccountCategory.REVENUE
    if is_revenue:
        is_favorable = variance >= 0
    else:
        is_favorable = variance <= 0
    return {
        'company': company,
        'account': account,
        'branch': branch,
        'department': department,
        'period': period_label,
        'budget': budget_amount,
        'actual': actual,
        'variance': variance,
        'variance_percent': variance_percent(budget_amount, actual),
        'is_favorable': is_favorable,
        'direction': 'REVENUE' if is_revenue else 'COST',
    }


def over_budget_company(company, start, end, threshold=Decimal('100')):
    """Baris yang melewati budget lebih dari threshold % (untuk notification).

    Melewati budget berarti actual > budget, baik untuk revenue maupun cost.
    """
    rows = budget_vs_actual([company], start, end)
    return [r for r in rows if r['budget'] > 0 and r['variance'] > 0
            and r['variance_percent'] > threshold]
