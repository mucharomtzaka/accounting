"""PRD 18 - Project accounting: cost posting, billing, profitability."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Q, Sum

from gl.engine import JournalBuilder, get_account
from gl.models import JournalSource


def _wip_account(project):
    return project.wip_account or get_account(project.company, 'PROJECT', 'WIP')


def post_project_cost(cost, user=None):
    """Dr WIP / Cr AP - biaya proyek masuk ke WIP, belum jadi beban P&L."""
    if cost.is_posted:
        raise ValidationError(f'Project cost {cost.pk} sudah diposting.')
    if cost.total_amount <= 0:
        raise ValidationError('Project cost harus lebih dari 0.')

    project = cost.project
    ap = get_account(project.company, 'AP', 'AP_CONTROL')

    journal = (
        JournalBuilder(
            project.company, cost.cost_date, source=JournalSource.INVENTORY,
            description=f'Project cost {project.project_code}: {cost.description}',
            branch=project.branch, department=project.department, project=project,
            reference=f'PC/{project.project_code}/{cost.pk}', user=user,
        )
        .debit(_wip_account(project), cost.total_amount,
               f'WIP - {cost.get_cost_type_display()}')
        .credit(ap, cost.total_amount, 'Utang - biaya proyek')
        .save(auto_post=True)
    )

    cost.journal = journal
    cost.is_posted = True
    cost.save(update_fields=['journal', 'is_posted'])
    return journal


def post_project_billing(billing, user=None):
    """Dr AR + Dr Retensi / Cr Revenue. Gross diakui penuh, retensi tahan di aset."""
    if billing.status == 'POSTED':
        raise ValidationError(f'Project billing {billing.billing_number} sudah diposting.')
    if billing.gross_amount <= 0:
        raise ValidationError('Billing harus lebih dari 0.')

    project = billing.project
    ar = get_account(project.company, 'AR', 'AR_CONTROL')
    revenue = get_account(project.company, 'PROJECT', 'REVENUE')

    builder = JournalBuilder(
        project.company, billing.billing_date, source=JournalSource.SALES_INVOICE,
        description=f'Project billing {project.project_code}/{billing.billing_number}',
        branch=project.branch, department=project.department, project=project,
        reference=f'PB/{project.project_code}/{billing.billing_number}', user=user,
    )
    builder.debit(ar, billing.net_amount, f'Piutang - {project.project_name}',
                  customer=project.customer)
    if billing.retention_amount > 0:
        retention = get_account(project.company, 'PROJECT', 'RETENTION')
        builder.debit(retention, billing.retention_amount, 'Piutang retensi proyek')
    builder.credit(revenue, billing.gross_amount, 'Pendapatan proyek')

    journal = builder.save(auto_post=True)
    billing.journal = journal
    billing.status = 'POSTED'
    billing.save(update_fields=['journal', 'status'])

    if billing.milestone:
        billing.milestone.billed_amount += billing.net_amount
        billing.milestone.is_billed = billing.milestone.remaining_billable <= 0
        billing.milestone.save(update_fields=['billed_amount', 'is_billed'])

    return journal


def project_report(company, start=None, end=None):
    """Per-project breakdown dari journal posted: revenue, cost, profit, margin.

    Cost = Dr pada akun beban (kategori 6) + Dr pada akun WIP. Biaya proyek
    dicatat ke WIP (asset) saat costing, bukan langsung ke beban — jadi tanpa
    WIP, cost selalu nol meski sudah ada biaya. Profit = revenue - cost.
    """
    from core.models import Project
    from gl.models import AccountCategory, AccountMapping, JournalLine, JournalStatus

    posted = [JournalStatus.POSTED, JournalStatus.RECONCILED]
    qs = JournalLine.objects.filter(
        journal__company=company, journal__status__in=posted, journal__project__isnull=False
    )
    if start:
        qs = qs.filter(journal__journal_date__gte=start)
    if end:
        qs = qs.filter(journal__journal_date__lte=end)

    wip_ids = set(
        AccountMapping.objects.filter(company=company, module='PROJECT', key='WIP')
        .values_list('account_id', flat=True)
    )

    rows = []
    for project in Project.objects.filter(company=company).order_by('project_code'):
        plines = qs.filter(journal__project=project)

        # revenue: credit-normal -> credit - debit
        revenue = sum(
            ((l.credit - l.debit) for l in plines.filter(
                account__account_category=AccountCategory.REVENUE)), Decimal('0'))

        # cost: beban + WIP
        breakdown = {}
        cost_lines = plines.filter(
            Q(account__account_category=AccountCategory.EXPENSE) | Q(account_id__in=wip_ids)
        )
        for l in cost_lines:
            net = l.debit - l.credit
            if net == 0:
                continue
            breakdown[l.account.account_name] = breakdown.get(l.account.account_name, Decimal('0')) + net
        total_cost = sum(breakdown.values(), Decimal('0'))

        rows.append({
            'project': project,
            'revenue': revenue,
            'cost': total_cost,
            'cost_breakdown': breakdown,
            'profit': revenue - total_cost,
            'margin': ((revenue - total_cost) / revenue * 100) if revenue else Decimal('0'),
        })
    return rows
