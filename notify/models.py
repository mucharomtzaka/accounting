"""PRD 33 - Notifications.

Dibuat oleh scanner (`scan_notifications`), dibaca oleh admin. Sifatnya
"once per object per condition" - scanner tidak boleh mengulang baris yang
sudah ada sehingga jumlah notifikasi tetap stabil saat dipanggil berkali-kali.
"""
from django.db import models

from core.models import TimeStampedModel
from django.utils.translation import gettext_lazy as _


class Severity(models.TextChoices):
    INFO = 'INFO', 'Info'
    WARNING = 'WARNING', 'Warning'
    ERROR = 'ERROR', 'Error'


class Notification(TimeStampedModel):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        ACKNOWLEDGED = 'ACKNOWLEDGED', 'Acknowledged'
        RESOLVED = 'RESOLVED', 'Resolved'

    company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE, related_name='notifications'
    )
    category = models.CharField(max_length=40, choices=[
        ('INVOICE_OVERDUE', 'Invoice Overdue'),
        ('PAYMENT_DUE', 'Payment Due'),
        ('APPROVAL_PENDING', 'Approval Pending'),
        ('BUDGET_EXCEEDED', 'Budget Exceeded'),
        ('BANK_RECON_EXCEPTION', 'Bank Reconciliation Exception'),
        ('INTERCOMPANY_MISMATCH', 'Intercompany Mismatch'),
        ('PERIOD_CLOSING', 'Period Closing'),
        ('FAILED_JOURNAL', 'Failed Journal Posting'),
        ('FAILED_INTEGRATION', 'Failed Integration'),
    ])
    severity = models.CharField(max_length=10, choices=Severity.choices, default=Severity.WARNING)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)

    # referensi ke objek sumber, supaya bisa drill-down dari admin
    source_model = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=64, blank=True)
    source_repr = models.CharField(max_length=255, blank=True)
    # fingerprint = kunci unik per kondisi per objek, dicegah duplikat oleh scanner
    fingerprint = models.CharField(max_length=255, unique=True, db_index=True)

    acknowledged_by = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Notification')
        verbose_name_plural = _('Notifications')
        ordering = ['-created_at']

    def __str__(self):
        return f'[{self.get_severity_display()}] {self.title}'