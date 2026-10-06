"""PRD 33 - Notification scanner.

Satu fungsi `scan()` menghasilkan notifikasi untuk 9 kondisi:
    invoice overdue, payment due, approval pending, budget exceeded,
    bank reconciliation exception, intercompany mismatch, period closing,
    failed journal, failed integration.

Setiap kategori mengembalikan dict berisi Notification yang BARU dibuat.
Baris yang sudah ada tidak diulang (lewat fingerprint unik), jadi scanner
aman dipanggil berkali-kali - sesuai kebutuhan cron harian.
"""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Notification, Severity


def _emit(company, category, severity, title, body, source_model='', source_id='',
          source_repr='', fingerprint=None):
    """Buat notifikasi kalau fingerprint-nya belum pernah ada."""
    if fingerprint is None:
        fingerprint = f'{category}:{source_model}:{source_id}'
    obj, created = Notification.objects.get_or_create(
        fingerprint=fingerprint,
        defaults={
            'company': company,
            'category': category,
            'severity': severity,
            'title': title[:255],
            'body': body,
            'source_model': source_model,
            'source_id': str(source_id),
            'source_repr': source_repr[:255],
        },
    )
    return obj if created else None


def scan_invoice_overdue(company, today=None):
    """Invoice lewat jatuh tempo & belum lunas."""
    from ar.models import CustomerInvoice

    today = today or timezone.localdate()
    created = []
    overdue = CustomerInvoice.objects.filter(
        company=company, due_date__lt=today,
        status__in=['POSTED', 'PARTIALLY_PAID', 'OVERDUE'],
    ).exclude(amount_paid__gte=models_f('total'))
    for inv in overdue:
        n = _emit(
            company, 'INVOICE_OVERDUE', Severity.WARNING,
            f'Invoice {inv.invoice_number} lewat jatuh tempo',
            f'{inv.customer.customer_name} - sisa Rp{(inv.outstanding * inv.exchange_rate):,.0f}, '
            f'lewat {(today - inv.due_date).days} hari.',
            source_model='CustomerInvoice', source_id=inv.pk, source_repr=inv.invoice_number,
        )
        if n:
            created.append(n)
    return created


def models_f(field):
    from django.db.models import F
    return F(field)


def scan_payment_due(company, today=None):
    """Tagihan supplier yang jatuh tempo minggu ini."""
    from ap.models import BillType, SupplierBill

    today = today or timezone.localdate()
    horizon = today + timedelta(days=7)
    created = []
    due = SupplierBill.objects.filter(
        company=company, bill_type=BillType.BILL,
        due_date__gte=today, due_date__lte=horizon,
        status__in=['POSTED', 'PARTIALLY_PAID'],
    ).exclude(amount_paid__gte=models_f('total'))
    for bill in due:
        n = _emit(
            company, 'PAYMENT_DUE', Severity.INFO,
            f'Tagihan {bill.bill_number} jatuh tempo',
            f'{bill.supplier.supplier_name} - sisa Rp{(bill.outstanding * bill.exchange_rate):,.0f}, '
            f'due {bill.due_date}.',
            source_model='SupplierBill', source_id=bill.pk, source_repr=bill.bill_number,
        )
        if n:
            created.append(n)
    return created


def scan_approval_pending(company, today=None):
    """Request approval yang menggantung > 1 hari."""
    from workflow.models import ApprovalRequest, WorkflowStatus

    cutoff = timezone.now() - timedelta(days=1)
    created = []
    pending = ApprovalRequest.objects.filter(
        workflow__company=company, status=WorkflowStatus.PENDING, created_at__lt=cutoff,
    )
    for req in pending:
        n = _emit(
            company, 'APPROVAL_PENDING', Severity.INFO,
            f'Approval menunggu: {req.object_repr}',
            f'{req.get_required_role_display() if hasattr(req, "get_required_role_display") else req.step.required_role}'
            f' - nominal Rp{req.amount:,.0f}, sudah menunggu lebih dari 1 hari.',
            source_model='ApprovalRequest', source_id=req.pk, source_repr=req.object_repr,
        )
        if n:
            created.append(n)
    return created


def scan_bank_recon_exception(company, today=None):
    """Baris statement yang belum match > 14 hari."""
    from bank.models import BankReconciliation

    today = today or timezone.localdate()
    cutoff = today - timedelta(days=14)
    created = []
    recs = BankReconciliation.objects.filter(
        bank_account__company=company, status='IN_PROGRESS', statement_date__lt=cutoff,
    )
    for rec in recs:
        unmatched = rec.statement_lines.filter(matched_transaction__isnull=True).count()
        if unmatched == 0:
            continue
        n = _emit(
            company, 'BANK_RECON_EXCEPTION', Severity.ERROR,
            f'Rekonsiliasi {rec.reconciliation_name} macet',
            f'{unmatched} baris statement belum di-match, statement date {rec.statement_date}.',
            source_model='BankReconciliation', source_id=rec.pk, source_repr=rec.reconciliation_name,
        )
        if n:
            created.append(n)
    return created


def scan_intercompany_mismatch(company, today=None):
    """Transaksi intercompany yang belum match."""
    from consolidation.models import IntercompanyStatus
    from consolidation.services import matching_exceptions

    created = []
    try:
        exceptions = matching_exceptions(company.group)
    except Exception:
        return created
    for exc in exceptions:
        tx = exc['transaction']
        if tx.source_company_id != company.pk and tx.target_company_id != company.pk:
            continue
        n = _emit(
            company, 'INTERCOMPANY_MISMATCH', Severity.ERROR,
            f'IC mismatch: {tx.transaction_number}',
            exc['message'],
            source_model='IntercompanyTransaction', source_id=tx.pk,
            source_repr=tx.transaction_number,
        )
        if n:
            created.append(n)
    return created


def scan_period_closing(company, today=None):
    """Period CLOSED pada bulan sebelumnya - ajakan buka tutup bulan ini."""
    from gl.models import AccountingPeriod, FiscalYear

    today = today or timezone.localdate()
    created = []
    if today.day > 10:
        return created  # notifikasi period closing hanya dibuat awal bulan
    # bulan sebelumnya = hari pertama bulan ini dikurangi 1
    first_this_month = today.replace(day=1)
    prev_month = (first_this_month - timedelta(days=1)).replace(day=1)
    prev_period = AccountingPeriod.objects.filter(
        company=company, status='CLOSED',
        start_date__year=prev_month.year, start_date__month=prev_month.month,
    ).first()
    current_period = AccountingPeriod.objects.filter(
        company=company, start_date__lte=today, end_date__gte=today,
    ).first()
    if prev_period and current_period:
        n = _emit(
            company, 'PERIOD_CLOSING', Severity.INFO,
            f'Period {current_period.period_code} siap ditutup',
            f'Period {prev_period.period_code} sudah CLOSED. '
            f'Pastikan rekonsiliasi & accrual beres sebelum tutup {current_period.period_code}.',
            source_model='AccountingPeriod', source_id=current_period.pk,
            source_repr=current_period.period_code,
        )
        if n:
            created.append(n)
    return created


def scan_failed_journal(company, today=None):
    """Journal POSTED yang tidak balance - integritas GLACY terancam."""
    from django.db.models import F, Sum
    from gl.models import JournalEntry, JournalStatus

    created = []
    broken = (
        JournalEntry.objects.filter(company=company, status=JournalStatus.POSTED)
        .annotate(d=Sum('lines__debit'), c=Sum('lines__credit'))
        .filter(d__isnull=False).exclude(d=F('c'))
    )
    for je in broken:
        n = _emit(
            company, 'FAILED_JOURNAL', Severity.ERROR,
            f'Journal {je.journal_number} tidak balance',
            f'Debit != Credit pada journal POSTED - posted via {je.get_source_display()}.',
            source_model='JournalEntry', source_id=je.pk, source_repr=je.journal_number,
        )
        if n:
            created.append(n)
    return created


def scan_budget_exceeded(company, today=None):
    """Budget yang terlewati lebih dari 100% (PRD 33 + 22)."""
    from core.budget_services import over_budget_company

    today = today or timezone.localdate()
    start = today.replace(month=1, day=1)
    created = []
    for row in over_budget_company(company, start, today, threshold=Decimal('100')):
        n = _emit(
            company, 'BUDGET_EXCEEDED', Severity.WARNING,
            f'Budget {row["account"].account_code} terlampaui',
            f'{row["account"].account_name}: budget {row["budget"]:,.0f} vs actual '
            f'{row["actual"]:,.0f} (+{row["variance_percent"]:.0f}%).',
            source_model='Account', source_id=row['account'].pk,
            source_repr=row['account'].account_code,
            fingerprint=f'BUDGET_EXCEEDED:{row["account"].pk}:{today:%Y-%m}',
        )
        if n:
            created.append(n)
    return created


def scan_failed_integration(company, today=None):
    """Placeholder - belum ada integrasi eksternal."""
    return []


SCANNERS = [
    ('invoice_overdue', scan_invoice_overdue),
    ('payment_due', scan_payment_due),
    ('approval_pending', scan_approval_pending),
    ('bank_recon_exception', scan_bank_recon_exception),
    ('intercompany_mismatch', scan_intercompany_mismatch),
    ('period_closing', scan_period_closing),
    ('failed_journal', scan_failed_journal),
    ('budget_exceeded', scan_budget_exceeded),
    ('failed_integration', scan_failed_integration),
]


def scan(company, today=None, only=None):
    """Jalankan semua scanner untuk satu company.

    only: iterable nama scanner yang mau dijalankan (default: semua).
    Return dict {nama_scanner: [Notification baru]}.
    """
    today = today or timezone.localdate()
    results = {}
    for name, fn in SCANNERS:
        if only and name not in only:
            continue
        try:
            results[name] = fn(company, today)
        except Exception as exc:
            results[name] = []
            results[f'{name}:error'] = [str(exc)]
    return results