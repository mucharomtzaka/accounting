from decimal import Decimal

from django.db import models

from core.models import TimeStampedModel
from django.utils.translation import gettext_lazy as _


class Project(TimeStampedModel):
    class Status(models.TextChoices):
        PLANNING = 'PLANNING', 'Planning'
        IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
        COMPLETED = 'COMPLETED', 'Completed'
        CLOSED = 'CLOSED', 'Closed'

    class Type(models.TextChoices):
        SERVICE = 'SERVICE', 'Service / Project'
        INTERNAL = 'INTERNAL', 'Internal / Capital'

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='projects')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.PROTECT, related_name='projects'
    )
    department = models.ForeignKey(
        'core.Department', null=True, blank=True, on_delete=models.PROTECT, related_name='projects'
    )
    customer = models.ForeignKey(
        'ar.Customer', null=True, blank=True, on_delete=models.PROTECT, related_name='projects'
    )
    project_code = models.CharField(max_length=30)
    project_name = models.CharField(max_length=255)
    project_type = models.CharField(max_length=10, choices=Type.choices, default=Type.SERVICE)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    contract_value = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    budget_cost = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    retention_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PLANNING)
    wip_account = models.ForeignKey(
        'gl.Account', null=True, blank=True, on_delete=models.PROTECT, related_name='+'
    )

    class Meta:
        verbose_name = _('Project')
        verbose_name_plural = _('Projects')
        ordering = ['company', 'project_code']
        unique_together = [('company', 'project_code')]

    def __str__(self):
        return f'{self.project_code} - {self.project_name}'

class ProjectCost(TimeStampedModel):
    """PRD 18 - Project cost (labor / material / overhead) -> Dr WIP, Cr AP."""

    class CostType(models.TextChoices):
        DIRECT = 'DIRECT', 'Direct Cost'
        LABOR = 'LABOR', 'Labor'
        MATERIAL = 'MATERIAL', 'Material'
        OVERHEAD = 'OVERHEAD', 'Overhead'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='costs')
    cost_date = models.DateField()
    cost_type = models.CharField(max_length=10, choices=CostType.choices, default=CostType.DIRECT)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=18, decimal_places=4, default=1)
    unit_price = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='project_costs'
    )
    is_posted = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Project Cost')
        verbose_name_plural = _('Project Costs')
        ordering = ['-cost_date', '-id']

    def __str__(self):
        return f'{self.project.project_code} {self.cost_type} {self.total_amount}'

    def save(self, *args, **kwargs):
        self.total_amount = (
            Decimal(self.quantity) * Decimal(self.unit_price)
        ).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)


class ProjectMilestone(TimeStampedModel):
    """PRD 18 - Milestone / progress billing basis."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    milestone_code = models.CharField(max_length=30)
    milestone_name = models.CharField(max_length=255)
    target_date = models.DateField(null=True, blank=True)
    percent_complete = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    billable_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    billed_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    is_billed = models.BooleanField(default=False)

    class Meta:
        verbose_name = _('Project Milestone')
        verbose_name_plural = _('Project Milestones')
        ordering = ['project', 'target_date']
        unique_together = [('project', 'milestone_code')]

    def __str__(self):
        return f'{self.project.project_code} - {self.milestone_name}'

    @property
    def remaining_billable(self):
        return self.billable_amount - self.billed_amount


class ProjectBilling(TimeStampedModel):
    """PRD 18 - Project billing -> Dr AR, Cr Revenue (net of retention)."""

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        POSTED = 'POSTED', 'Posted'
        VOID = 'VOID', 'Void'

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='billings')
    milestone = models.ForeignKey(
        ProjectMilestone, null=True, blank=True, on_delete=models.SET_NULL, related_name='billings'
    )
    billing_number = models.CharField(max_length=30)
    billing_date = models.DateField()
    gross_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    retention_percent = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    retention_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    customer_invoice = models.ForeignKey(
        'ar.CustomerInvoice', null=True, blank=True, on_delete=models.SET_NULL, related_name='project_billings'
    )
    journal = models.ForeignKey(
        'gl.JournalEntry', null=True, blank=True, on_delete=models.SET_NULL, related_name='project_billings'
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = _('Project Billing')
        verbose_name_plural = _('Project Billings')
        ordering = ['-billing_date', '-id']
        unique_together = [('project', 'billing_number')]

    def __str__(self):
        return f'{self.project.project_code}/{self.billing_number}'

    def save(self, *args, **kwargs):
        retention = (self.gross_amount * self.retention_percent / Decimal('100')).quantize(Decimal('0.01'))
        self.retention_amount = retention
        self.net_amount = self.gross_amount - retention
        super().save(*args, **kwargs)
