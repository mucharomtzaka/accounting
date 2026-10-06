"""PRD 30 - Approval Workflow dengan configurable limit."""
from django.db import models

from core.models import TimeStampedModel, User
from django.utils.translation import gettext_lazy as _


class WorkflowStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'
    CANCELLED = 'CANCELLED', 'Cancelled'


class ApprovalWorkflow(TimeStampedModel):
    company = models.ForeignKey(
        'core.Company', on_delete=models.CASCADE, related_name='approval_workflows'
    )
    name = models.CharField(max_length=150)
    MODEL_CHOICES = [
        ('SupplierBill', 'Supplier Bill'),
        ('SupplierPayment', 'Supplier Payment'),
        ('CustomerInvoice', 'Customer Invoice'),
        ('CustomerPayment', 'Customer Payment'),
        ('IntercompanyTransaction', 'Intercompany Transaction'),
    ]

    model_name = models.CharField(
        max_length=100, choices=MODEL_CHOICES,
        help_text='Dokumen yang memakai approval workflow ini',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Approval Workflow')
        verbose_name_plural = _('Approval Workflows')
        ordering = ['company', 'name']

    def __str__(self):
        return f'{self.company.company_code} - {self.name}'


class ApprovalStep(TimeStampedModel):
    """PRD 30 - < 10jt Manager, 10-100jt Finance Manager, > 100jt Director."""

    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.CASCADE, related_name='steps')
    step_order = models.PositiveSmallIntegerField(default=10)
    required_role = models.CharField(max_length=20, choices=User.Role.choices)
    min_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    max_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True,
                                     help_text='Kosong = tanpa batas atas')
    can_reject = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Approval Step')
        verbose_name_plural = _('Approval Steps')
        ordering = ['workflow', 'step_order']

    def __str__(self):
        return f'{self.step_order} {self.get_required_role_display()} {self.min_amount}-{self.max_amount or "∞"}'

    def matches(self, amount):
        if amount < self.min_amount:
            return False
        return self.max_amount is None or amount <= self.max_amount


class ApprovalRequest(TimeStampedModel):
    workflow = models.ForeignKey(ApprovalWorkflow, on_delete=models.CASCADE, related_name='requests')
    step = models.ForeignKey(ApprovalStep, on_delete=models.PROTECT, related_name='requests')
    requester = models.ForeignKey(User, on_delete=models.PROTECT, related_name='approval_requests_made')
    approver = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='approval_requests_assigned',
    )
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    object_repr = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    currency = models.ForeignKey('core.Currency', on_delete=models.PROTECT, default='IDR')
    status = models.CharField(max_length=15, choices=WorkflowStatus.choices, default=WorkflowStatus.PENDING)
    comments = models.TextField(blank=True)
    action_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _('Approval Request')
        verbose_name_plural = _('Approval Requests')
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.object_repr or self.object_id} - {self.get_status_display()}'