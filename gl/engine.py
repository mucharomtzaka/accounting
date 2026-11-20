"""PRD 12 - Automatic Accounting Engine + PRD 37 Data Integrity Rules."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .errors import (
    AccountingError, PeriodLockedError, UnbalancedJournalError, PeriodNotFoundError,
)
from .models import (
    Account, AccountMapping, AccountingPeriod, JournalCategory, JournalEntry, JournalLine, JournalSource, JournalStatus,
)


def get_period(company, on_date):
    period = AccountingPeriod.objects.filter(company=company, start_date__lte=on_date, end_date__gte=on_date).first()
    if not period:
        raise PeriodNotFoundError(
            f'Tidak ada accounting period untuk {company.company_code} pada tanggal {on_date}. '
            'Buat fiscal year dan period terlebih dahulu.'
        )
    return period


def get_account(company, module, key, required=True):
    mapping = AccountMapping.objects.filter(
        company=company, module=module, key=key, is_active=True
    ).select_related('account').first()
    if mapping:
        return mapping.account
    if not required:
        return None
    raise AccountingError(
        f'Account mapping "{module}/{key}" belum dikonfigurasi untuk {company.company_code}.'
    )


class JournalBuilder:
    """Kumpulkan line, lalu post sebagai satu journal balance."""

    def __init__(self, company, journal_date, source=JournalSource.MANUAL, description='',
                 currency='IDR', exchange_rate=Decimal('1'), branch=None, department=None,
                 cost_center=None, project=None, reference='', user=None, category=None):
        self.company = company
        self.journal_date = journal_date
        self.source = source
        self.category = category or (
            JournalCategory.TAX if source == JournalSource.TAX else JournalCategory.INTERNAL
        )
        self.description = description
        self.currency = currency
        self.exchange_rate = Decimal(exchange_rate or 1)
        self.branch = branch
        self.department = department
        self.cost_center = cost_center
        self.project = project
        self.reference = reference
        self.user = user
        self._lines = []

    def debit(self, account, amount, description='', **dimensions):
        return self._add(account, amount, 'D', description, **dimensions)

    def credit(self, account, amount, description='', **dimensions):
        return self._add(account, amount, 'C', description, **dimensions)

    def _add(self, account, amount, side, description, **dimensions):
        amount = abs(Decimal(amount))
        self._lines.append((account, side, amount, description, dimensions))
        return self

    @property
    def total(self):
        return sum((amount for _, _, amount, _, _ in self._lines), Decimal('0'))

    @property
    def is_balanced(self):
        debit = sum((a for _, s, a, _, _ in self._lines if s == 'D'), Decimal('0'))
        credit = sum((a for _, s, a, _, _ in self._lines if s == 'C'), Decimal('0'))
        return debit == credit

    def save(self, status=JournalStatus.DRAFT, auto_post=False):
        if not self._lines:
            raise AccountingError('Journal tidak memiliki line.')
        if not self.is_balanced:
            raise UnbalancedJournalError('Total debit tidak sama dengan total credit.')

        period = get_period(self.company, self.journal_date)
        if period.is_locked:
            raise PeriodLockedError(
                f'Period {period.period_code} berstatus CLOSED. '
                'Reopen period atau gunakan tanggal lain.'
            )

        if auto_post and status == JournalStatus.DRAFT:
            status = JournalStatus.POSTED

        with transaction.atomic():
            journal = JournalEntry.objects.create(
                company=self.company,
                branch=self.branch,
                department=self.department,
                cost_center=self.cost_center,
                project=self.project,
                period=period,
                journal_number=self._next_number(period),
                journal_date=self.journal_date,
                source=self.source,
                category=self.category,
                status=status,
                currency_id=self.currency,
                exchange_rate=self.exchange_rate,
                description=self.description,
                reference=self.reference,
            )
            for index, (account, side, amount, desc, dims) in enumerate(self._lines):
                JournalLine.objects.create(
                    journal=journal,
                    line_no=(index + 1) * 10,
                    account=account,
                    branch=dims.get('branch', self.branch),
                    department=dims.get('department', self.department),
                    cost_center=dims.get('cost_center', self.cost_center),
                    project=dims.get('project', self.project),
                    customer=dims.get('customer'),
                    supplier=dims.get('supplier'),
                    description=desc,
                    debit=amount if side == 'D' else Decimal('0'),
                    credit=amount if side == 'C' else Decimal('0'),
                    amount_base=amount * self.exchange_rate,
                )
            if status == JournalStatus.POSTED:
                self._mark_posted(journal)
        return journal

    def _next_number(self, period):
        prefix = f'JE/{period.period_code}/'
        last = (
            JournalEntry.objects.filter(company=self.company, journal_number__startswith=prefix)
            .order_by('-journal_number')
            .values_list('journal_number', flat=True)
            .first()
        )
        seq = int(last.rsplit('/', 1)[1]) + 1 if last else 1
        return f'{prefix}{seq:05d}'

    @staticmethod
    def _mark_posted(journal):
        now = timezone.now()
        JournalEntry.objects.filter(pk=journal.pk).update(
            status=JournalStatus.POSTED, posted_at=now,
        )
        journal.status = JournalStatus.POSTED
        journal.posted_at = now


def post_journal(company, journal_date, lines, source=JournalSource.MANUAL, **kwargs):
    """lines: iterable of (account, side, amount, description)"""
    builder = JournalBuilder(company, journal_date, source=source, **kwargs)
    for account, side, amount, description in lines:
        builder._add(account, amount, side, description)
    return builder.save(auto_post=True)


def reverse_journal(journal, user=None, reason='', on_date=None):
    """PRD 31 - koreksi via reversal, bukan edit."""
    if journal.status != JournalStatus.POSTED:
        raise AccountingError('Hanya journal POSTED yang bisa di-reversal.')

    builder = JournalBuilder(
        company=journal.company,
        journal_date=on_date or date.today(),
        source=journal.source,
        category=journal.category,
        description=f'Reversal: {journal.description or journal.journal_number}',
        currency=journal.currency_id,
        exchange_rate=journal.exchange_rate,
        branch=journal.branch,
        department=journal.department,
        cost_center=journal.cost_center,
        project=journal.project,
        reference=f'REV/{journal.journal_number}',
        user=user,
    )
    for line in journal.lines.select_related('account'):
        builder._add(
            line.account, line.debit or line.credit,
            'C' if line.debit else 'D',
            f'Reversal {line.description}',
            branch=line.branch, department=line.department,
            cost_center=line.cost_center, project=line.project,
            customer=line.customer, supplier=line.supplier,
        )
    reversed_journal = builder.save(auto_post=True)
    JournalEntry.objects.filter(pk=reversed_journal.pk).update(reversed_from=journal)

    # Journal asal tetap POSTED (dibatalkan oleh pasangannya), hanya ditandai reversed.
    JournalEntry.objects.filter(pk=journal.pk).update(
        is_reversed=True, voided_by=user, void_reason=reason or 'Reversed',
    )
    journal.is_reversed = True
    return reversed_journal


def account_balance(company, account, start=None, end=None):
    """Saldo satu akun dalam base currency."""
    from django.db.models import Sum

    qs = JournalLine.objects.filter(journal__company=company, journal__status__in=[
        JournalStatus.POSTED, JournalStatus.RECONCILED
    ], account=account)
    if start:
        qs = qs.filter(journal__journal_date__gte=start)
    if end:
        qs = qs.filter(journal__journal_date__lte=end)
    totals = qs.aggregate(d=Sum('debit'), c=Sum('credit'))
    return (totals['d'] or Decimal('0')) - (totals['c'] or Decimal('0'))


def ensure_postable(account):
    if not account.is_postable:
        raise AccountingError(f'Account {account.account_code} adalah akun header, tidak bisa diposting.')
    if not account.is_active:
        raise AccountingError(f'Account {account.account_code} tidak aktif.')