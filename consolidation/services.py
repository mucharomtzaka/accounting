"""PRD 24.3 - Intercompany matching & elimination."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    ConsolidationRun, IntercompanyStatus, IntercompanyTransaction,
)


def post_intercompany(tx, user=None):
    """PRD 24.1 - Dr IC Receivable / Cr IC Revenue di source company."""
    if tx.status != IntercompanyStatus.DRAFT:
        raise ValidationError(f'{tx.transaction_number} sudah posted.')
    if tx.source_company_id == tx.target_company_id:
        raise ValidationError('Source dan target company tidak boleh sama.')

    from gl.engine import JournalBuilder, get_account
    from gl.models import JournalSource

    ic_receivable = get_account(tx.source_company, 'INTERCOMPANY', 'IC_RECEIVABLE')
    if tx.tx_type in ('SALES', 'TRANSFER'):
        credit = get_account(tx.source_company, 'INTERCOMPANY', 'IC_REVENUE')
        debit = ic_receivable
    else:
        credit = get_account(tx.source_company, 'INTERCOMPANY', 'IC_PAYABLE')
        debit = get_account(tx.source_company, 'INTERCOMPANY', 'IC_EXPENSE')

    journal = JournalBuilder(
        tx.source_company, tx.transaction_date, source=JournalSource.INTERCOMPANY,
        description=f'{tx.get_tx_type_display()} to {tx.target_company.company_code}',
        currency=tx.currency_id, exchange_rate=tx.exchange_rate,
        reference=tx.transaction_number, user=user,
    )
    journal.debit(debit, tx.amount, 'Intercompany debit')
    journal.credit(credit, tx.amount, 'Intercompany credit')
    posted = journal.save(auto_post=True)

    IntercompanyTransaction.objects.filter(pk=tx.pk).update(
        status=IntercompanyStatus.POSTED, journal=posted
    )
    tx.status = IntercompanyStatus.POSTED
    tx.journal = posted
    return posted


def matching_exceptions(group=None):
    """PRD 24.3 - A receivable harus = B payable. Selisih = exception."""
    qs = IntercompanyTransaction.objects.select_related(
        'source_company', 'target_company'
    ).exclude(status=IntercompanyStatus.ELIMINATED)
    if group:
        qs = qs.filter(group=group)

    exceptions = []
    matched_ids = set()
    for tx in qs.filter(status__in=[IntercompanyStatus.POSTED, IntercompanyStatus.MATCHED]):
        counter = qs.filter(
            source_company=tx.target_company, target_company=tx.source_company,
            status__in=[IntercompanyStatus.POSTED, IntercompanyStatus.MATCHED],
        ).exclude(pk=tx.pk).order_by('id').first()
        if counter is None:
            exceptions.append({'transaction': tx, 'type': 'NO_COUNTERPART',
                               'message': f'{tx.transaction_number}: tidak ada transaksi balasan di {tx.target_company.company_code}.'})
            continue
        if counter.pk in matched_ids:
            continue
        diff = tx.amount_base - counter.amount_base
        if abs(diff) > Decimal('0.01'):
            exceptions.append({'transaction': tx, 'counterpart': counter, 'type': 'AMOUNT_MISMATCH',
                               'difference': diff,
                               'message': f'{tx.transaction_number} vs {counter.transaction_number}: selisih {diff}.'})
        else:
            matched_ids.update({tx.pk, counter.pk})
            IntercompanyTransaction.objects.filter(pk__in=[tx.pk, counter.pk]).update(
                status=IntercompanyStatus.MATCHED
            )
    return exceptions


@transaction.atomic
def eliminate(run, user=None):
    """PRD 25 - eliminasi saat consolidation. Reversal di masing-masing company."""
    if run.status == ConsolidationRun.Status.COMPLETED:
        raise ValidationError(f'{run.run_name} sudah selesai.')

    exceptions = matching_exceptions(run.group)
    pending = IntercompanyTransaction.objects.filter(
        group=run.group, status=IntercompanyStatus.MATCHED,
        transaction_date__range=(run.period_from, run.period_to),
    ).exclude(eliminated_in=run)

    count = 0
    total = Decimal('0')
    for tx in pending:
        from gl.engine import reverse_journal

        if tx.journal_id:
            reverse_journal(tx.journal, user=user, reason=f'Consolidation {run.run_name}', on_date=run.period_to)
        IntercompanyTransaction.objects.filter(pk=tx.pk).update(
            status=IntercompanyStatus.ELIMINATED, eliminated_in=run
        )
        count += 1
        total += tx.amount_base

    ConsolidationRun.objects.filter(pk=run.pk).update(
        eliminated_count=count, eliminated_amount=total,
        status=ConsolidationRun.Status.COMPLETED, completed_at=timezone.now(),
    )
    return count, total, exceptions


def consolidated_pnl(run, companies):
    """PRD 25 - consolidated P&L: sum per company lalu hapus efek intercompany."""
    from reports.engine import profit_and_loss

    data = profit_and_loss(companies, run.period_from, run.period_to)
    ic_revenue_accounts = _ic_accounts(run.group, 'INTERCOMPANY', 'IC_REVENUE')
    ic_expense_accounts = _ic_accounts(run.group, 'INTERCOMPANY', 'IC_EXPENSE')

    revenue = data['revenue']
    expense = data['expense']
    ic_revenue = sum(
        abs(r['amount']) for r in data['revenue_lines']
        if r['account'].id in ic_revenue_accounts
    )
    ic_expense = sum(
        r['amount'] for r in data['expense_lines']
        if r['account'].id in ic_expense_accounts
    )
    return {
        'revenue': revenue - ic_revenue,
        'cogs': data['cogs'],
        'expense': expense - ic_expense,
        'ic_revenue': ic_revenue,
        'ic_expense': ic_expense,
        'gross_profit': (revenue - ic_revenue) - data['cogs'],
        'net_profit': (revenue - ic_revenue) - data['cogs'] - (expense - ic_expense),
    }


def _ic_accounts(group, module, key):
    from core.models import Company
    from gl.models import AccountMapping

    company_ids = Company.objects.filter(group=group).values_list('id', flat=True)
    return set(AccountMapping.objects.filter(
        company_id__in=company_ids, module=module, key=key
    ).values_list('account_id', flat=True))