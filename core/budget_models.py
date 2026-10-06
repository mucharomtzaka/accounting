"""PRD 22 - Budgeting: annual/monthly budget + budget vs actual."""
from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models

from core.models import AuditMixin, TimeStampedModel
from django.utils.translation import gettext_lazy as _


class BudgetType(models.TextChoices):
    ANNUAL = 'ANNUAL', 'Annual'
    MONTHLY = 'MONTHLY', 'Monthly'


class BudgetStatus(models.TextChoices):
    DRAFT = 'DRAFT', 'Draft'
    SUBMITTED = 'SUBMITTED', 'Submitted'
    APPROVED = 'APPROVED', 'Approved'
    REJECTED = 'REJECTED', 'Rejected'


class Budget(TimeStampedModel, AuditMixin):
    """Satu baris budget per kombinasi dimensi + periode.

    Dimensi opsional supaya bisa buat budget setingkat company saja
    (sales budget) atau selengkap per cost center + project.
    """

    company = models.ForeignKey('core.Company', on_delete=models.CASCADE, related_name='budgets')
    branch = models.ForeignKey(
        'core.Branch', null=True, blank=True, on_delete=models.CASCADE, related_name='budgets'
    )
    department = models.ForeignKey(
        'core.Department', null=True, blank=True, on_delete=models.CASCADE, related_name='budgets'
    )
    account = models.ForeignKey(
        'gl.Account', on_delete=models.CASCADE, related_name='budgets',
        help_text='Akun yang dibudgetkan (revenue/expense)',
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.CASCADE, related_name='budgets'
    )
    project = models.ForeignKey(
        'core.Project', null=True, blank=True, on_delete=models.CASCADE, related_name='budgets'
    )

    fiscal_year = models.CharField(max_length=4)
    budget_type = models.CharField(max_length=10, choices=BudgetType.choices, default=BudgetType.ANNUAL)
    period = models.CharField(
        max_length=7, blank=True, default='',
        help_text='Bulan (YYYY-MM) untuk budget bulanan; kosong untuk annual',
    )
    amount = models.DecimalField(max_digits=20, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    revision = models.PositiveIntegerField(default=1)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=BudgetStatus.choices, default=BudgetStatus.DRAFT)

    class Meta:
        verbose_name = _('Budget')
        verbose_name_plural = _('Budgets')
        ordering = ['company', 'fiscal_year', 'account__account_code']

    def __str__(self):
        scope = self.company.company_code
        if self.branch:
            scope += f'/{self.branch.branch_code}'
        if self.department:
            scope += f'/{self.department.department_code}'
        return f'{scope} {self.fiscal_year} {self.account} {self.amount}'

    @property
    def key(self):
        return (
            self.company_id, self.branch_id, self.department_id, self.account_id,
            self.cost_center_id, self.project_id, self.fiscal_year,
            self.budget_type, self.period,
        )