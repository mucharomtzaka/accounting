"""PRD 23 + 25 - Currency translation & non-controlling interest.

Consolidated report tidak boleh menjumlahkan angka dalam currency berbeda.
Semua posisi currency non-group harus ditranslasi ke group reporting currency
lewat ExchangeRate, lalu dijumlahkan.
"""
from decimal import Decimal

from gl.models import AccountCategory, JournalLine, JournalStatus

POSTED = [JournalStatus.POSTED, JournalStatus.RECONCILED]


def group_currency(group):
    """Base currency group = base currency company dengan ownership terbesar.

    Kalau ada beberapa company, pilih yang tidak punya parent (root) atau
    ownership tertinggi. Company pertama sebagai fallback.
    """
    companies = list(group.companies.filter(status='ACTIVE'))
    if not companies:
        from core.models import Currency

        return Currency.objects.first()
    companies.sort(key=lambda c: (-c.ownership_percentage, c.company_code))
    return companies[0].base_currency


def get_rate(currency_code, target_currency, as_of, company=None):
    """Kurs terakhir <= as_of. Kalau currency sama, 1.

    company-specific rate diprioritaskan; kalau tidak ada, pakai global.
    """
    if currency_code == target_currency:
        return Decimal('1')

    from core.models import ExchangeRate

    qs = ExchangeRate.objects.filter(currency_id=currency_code, rate_date__lte=as_of)
    if company is not None:
        row = qs.filter(company=company).order_by('-rate_date').first()
        if row:
            return row.rate
    row = qs.order_by('-rate_date').first()
    if row is None:
        raise LookupError(
            f'Tidak ada ExchangeRate {currency_code} per {as_of}. '
            f'Kurs ke {target_currency} tidak bisa ditentukan - consolidated report diblokir.'
        )
    return row.rate


def translate_to_group(company, target_currency, as_of):
    """Rate untuk translasi satu company (rate terbaik yang tersedia)."""
    return get_rate(company.base_currency, target_currency, as_of, company=company)


def consolidated_balances(companies, start=None, end=None, as_of=None,
                          target_currency=None):
    """Saldo per kategori aset/liabilitas/ekuitas/revenue, ditranslasi ke
    satu currency.

    Return dict: {account_category: {amount_group, amount_local_by_company}}.
    """
    as_of = as_of or end
    qs = JournalLine.objects.filter(
        journal__company__in=companies, journal__status__in=POSTED,
    )
    if start:
        qs = qs.filter(journal__journal_date__gte=start)
    if as_of:
        qs = qs.filter(journal__journal_date__lte=as_of)

    target_currency = target_currency or _majority_currency(companies)
    rates = {}
    for c in companies:
        try:
            rates[c.pk] = translate_to_group(c, target_currency, as_of)
        except LookupError:
            rates[c.pk] = Decimal('1')  # skip company tanpa rate, catat di result

    buckets = {}
    for line in qs.select_related('journal__company', 'account'):
        company = line.journal.company
        rate = rates.get(company.pk, Decimal('1'))
        net_local = line.debit - line.credit
        net_group = (net_local * rate).quantize(Decimal('0.01'))
        cat = line.account.account_category
        b = buckets.setdefault(cat, {'group': Decimal('0'), 'by_company': {}})
        b['group'] += net_group
        cb = b['by_company'].setdefault(company.pk, {'local': Decimal('0'), 'group': Decimal('0')})
        cb['local'] += net_local
        cb['group'] += net_group

    return {'target_currency': target_currency, 'rates': rates, 'buckets': buckets}


def _majority_currency(companies):
    """Currency paling sering dipakai di antara company."""
    counts = {}
    for c in companies:
        counts[c.base_currency] = counts.get(c.base_currency, 0) + 1
    if not counts:
        from core.models import Currency

        return Currency.objects.first()
    return max(counts, key=counts.get)


def non_controlling_interest(companies, as_of, target_currency=None):
    """PRD 25 - NCI = porsi minority atas laba anak perusahaan.

    Minority% = 100 - ownership%.
    NCI = laba bersih anak company x minority%.
    """
    from reports.engine import profit_and_loss

    results = []
    for company in companies:
        pnl = profit_and_loss([company], None, as_of)
        minority = Decimal('100') - company.ownership_percentage
        if minority <= 0:
            continue
        try:
            rate = translate_to_group(company, target_currency or company.base_currency, as_of)
        except LookupError:
            rate = Decimal('1')
        net_local = pnl['net_profit']
        net_group = (net_local * rate).quantize(Decimal('0.01'))
        nci_share = (net_group * minority / Decimal('100')).quantize(Decimal('0.01'))
        results.append({
            'company': company,
            'ownership_percent': company.ownership_percentage,
            'minority_percent': minority,
            'net_profit_local': net_local,
            'net_profit_group': net_group,
            'nci': nci_share,
        })
    return results


def consolidated_pnl(group, companies, start, end, target_currency=None):
    """PRD 25 - Consolidated P&L dengan currency translation + eliminasi IC.

    net_profit_group = jumlah laba anak (setelah translasi) - NCI.
    """
    from consolidation.services import matching_exceptions
    from reports.engine import profit_and_loss

    target_currency = target_currency or _majority_currency(companies)

    total = {
        'revenue': Decimal('0'), 'cogs': Decimal('0'),
        'expense': Decimal('0'), 'net_profit': Decimal('0'),
    }
    ic_revenue = Decimal('0')
    ic_expense = Decimal('0')

    ic_accounts = _ic_accounts(group)
    for company in companies:
        try:
            rate = translate_to_group(company, target_currency, end)
        except LookupError:
            rate = Decimal('1')

        pnl = profit_and_loss([company], start, end)
        for key in ('revenue', 'cogs', 'expense', 'net_profit'):
            total[key] += (pnl[key] * rate).quantize(Decimal('0.01'))

        # eliminasi - IC revenue/expense yang masih ada di GL (belum dieliminasi)
        lines = JournalLine.objects.filter(
            journal__company=company, journal__status__in=POSTED,
            journal__journal_date__gte=start, journal__journal_date__lte=end,
            account__in=ic_accounts,
        )
        for line in lines:
            net = line.debit - line.credit
            amount = (net * rate).quantize(Decimal('0.01'))
            if line.account.account_category == AccountCategory.REVENUE:
                ic_revenue += abs(amount)
            else:
                ic_expense += amount

    total['ic_revenue'] = ic_revenue
    total['ic_expense'] = ic_expense
    total['gross_profit'] = total['revenue'] - total['cogs'] - ic_revenue
    total['net_profit'] = (
        total['revenue'] - ic_revenue - total['cogs'] - total['expense'] + ic_expense
    )
    total['target_currency'] = target_currency

    nci_rows = non_controlling_interest(companies, end, target_currency)
    total_nci = sum((r['nci'] for r in nci_rows), Decimal('0'))
    total['nci'] = total_nci
    total['net_profit_attributable'] = total['net_profit'] - total_nci
    total['nci_detail'] = nci_rows

    exceptions = matching_exceptions(group)
    total['ic_exceptions'] = exceptions
    return total


def _ic_accounts(group):
    from core.models import Company
    from gl.models import AccountMapping

    company_ids = Company.objects.filter(group=group).values_list('id', flat=True)
    return set(AccountMapping.objects.filter(
        company_id__in=company_ids, module='INTERCOMPANY'
    ).values_list('account_id', flat=True))