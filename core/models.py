from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditMixin(models.Model):
    """PRD 31 - Audit Trail: siapa membuat, mengubah, dan kapan."""

    created_by = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )
    updated_by = models.ForeignKey(
        'core.User', null=True, blank=True, on_delete=models.SET_NULL, related_name='+'
    )

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        if user and user.is_authenticated:
            if self.pk is None:
                self.created_by = user
            else:
                self.updated_by = user
        super().save(*args, **kwargs)


class User(AbstractUser):
    class Role(models.TextChoices):
        SUPER_ADMIN = 'SUPER_ADMIN', 'Super Admin'
        COMPANY_OWNER = 'COMPANY_OWNER', 'Company Owner'
        GROUP_FINANCE = 'GROUP_FINANCE', 'Group Finance'
        COMPANY_FINANCE = 'COMPANY_FINANCE', 'Company Finance'
        ACCOUNTANT = 'ACCOUNTANT', 'Accountant'
        AR_OFFICER = 'AR_OFFICER', 'AR Officer'
        AP_OFFICER = 'AP_OFFICER', 'AP Officer'
        TREASURY = 'TREASURY', 'Treasury'
        TAX_OFFICER = 'TAX_OFFICER', 'Tax Officer'
        BUDGET_CONTROLLER = 'BUDGET_CONTROLLER', 'Budget Controller'
        PROJECT_FINANCE = 'PROJECT_FINANCE', 'Project Finance'
        AUDITOR = 'AUDITOR', 'Auditor'
        MANAGEMENT = 'MANAGEMENT', 'Management'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.ACCOUNTANT)

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')

    def allowed_companies(self, request=None):
        """Company yang boleh diakses user (Rule 1).

        Kalau `request` diberikan dan session punya `active_company_id` yang
        valid (masih dalam allowed), queryset dibatasi ke company itu saja.
        Tidak pernah keluar dari allowed — switcher tidak jadi loophole.
        """
        if self.is_superuser:
            qs = Company.objects.all()
        else:
            qs = Company.objects.filter(user_access__user=self, user_access__is_active=True)
        if request is not None:
            cid = request.session.get('active_company_id')
            if cid and qs.filter(pk=cid).exists():
                return qs.filter(pk=cid)
        return qs

    @property
    def is_group_level(self):
        return self.role in (self.Role.SUPER_ADMIN, self.Role.GROUP_FINANCE)


class Group(TimeStampedModel):
    name = models.CharField(max_length=150, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Group')
        verbose_name_plural = _('Groups')

    def __str__(self):
        return self.name


class Company(TimeStampedModel, AuditMixin):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    group = models.ForeignKey(Group, on_delete=models.PROTECT, related_name='companies')
    company_code = models.CharField(max_length=20, unique=True)
    legal_name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=150)
    npwp = models.CharField(max_length=30, blank=True)
    nib = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    country = models.CharField(max_length=100, default='Indonesia')
    province = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=150, blank=True)
    base_currency = models.CharField(max_length=3, default='IDR')
    fiscal_year_start = models.IntegerField(default=1, help_text='Bulan mulai fiscal year (1-12)')
    tax_registration = models.CharField(max_length=50, blank=True)
    ownership_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, default=100,
        help_text='Untuk konsolidasi: persentase kepemilikan group',
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Company')
        verbose_name_plural = _('Companies')
        ordering = ['company_code']

    def __str__(self):
        return f'{self.company_code} - {self.display_name}'


class UserCompanyAccess(models.Model):
    """PRD 5.2 / Rule 1 - company isolation per user."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='user_access')
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='user_access')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('User Company Access')
        verbose_name_plural = _('User Company Access')
        unique_together = [('user', 'company')]

    def __str__(self):
        return f'{self.user} -> {self.company}'


class Branch(TimeStampedModel, AuditMixin):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='branches')
    branch_code = models.CharField(max_length=20)
    branch_name = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    manager = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name='managed_branches'
    )
    cost_center = models.ForeignKey(
        'core.CostCenter', null=True, blank=True, on_delete=models.SET_NULL, related_name='branches'
    )
    # PRD 6: assign warehouse per branch. String ref supaya core tidak perlu
    # mengimpor inventory (inventory sudah impor core).
    default_warehouse = models.ForeignKey(
        'inventory.Warehouse', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='default_branches',
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Branch')
        verbose_name_plural = _('Branches')
        ordering = ['company', 'branch_code']
        unique_together = [('company', 'branch_code')]

    def __str__(self):
        return f'{self.branch_code} - {self.branch_name}'


class UserBranchAccess(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='branch_access')
    branch = models.ForeignKey(Branch, on_delete=models.CASCADE, related_name='user_access')
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('User Branch Access')
        verbose_name_plural = _('User Branch Access')
        unique_together = [('user', 'branch')]


class Department(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='departments')
    department_code = models.CharField(max_length=20)
    department_name = models.CharField(max_length=150)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Department')
        verbose_name_plural = _('Departments')
        ordering = ['company', 'department_code']
        unique_together = [('company', 'department_code')]

    def __str__(self):
        return f'{self.department_code} - {self.department_name}'


class CostCenter(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = 'ACTIVE', 'Active'
        INACTIVE = 'INACTIVE', 'Inactive'

    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='cost_centers')
    department = models.ForeignKey(
        Department, null=True, blank=True, on_delete=models.SET_NULL, related_name='cost_centers'
    )
    branch = models.ForeignKey(
        Branch, null=True, blank=True, on_delete=models.SET_NULL, related_name='cost_centers'
    )
    cost_center_code = models.CharField(max_length=20)
    cost_center_name = models.CharField(max_length=150)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = _('Cost Center')
        verbose_name_plural = _('Cost Centers')
        ordering = ['company', 'cost_center_code']
        unique_together = [('company', 'cost_center_code')]

    def __str__(self):
        return f'{self.cost_center_code} - {self.cost_center_name}'


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True)
    name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=10, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = _('Currency')
        verbose_name_plural = _('Currencies')

    def __str__(self):
        return self.code


class ExchangeRate(models.Model):
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name='rates')
    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.CASCADE, related_name='exchange_rates'
    )
    rate_date = models.DateField(default=timezone.localdate)
    rate = models.DecimalField(max_digits=20, decimal_places=8)

    class Meta:
        verbose_name = _('Exchange Rate')
        verbose_name_plural = _('Exchange Rates')
        ordering = ['-rate_date']

    def __str__(self):
        scope = self.company.company_code if self.company else 'GLOBAL'
        return f'{self.currency_id} {scope} {self.rate_date} = {self.rate}'


class ApprovalLimit(TimeStampedModel):
    """PRD 30 - configurable approval limit."""

    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.CASCADE, related_name='approval_limits'
    )
    min_amount = models.DecimalField(max_digits=20, decimal_places=2, default=0)
    max_amount = models.DecimalField(max_digits=20, decimal_places=2, null=True, blank=True)
    required_role = models.CharField(max_length=20, choices=User.Role.choices)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, default='IDR')

    class Meta:
        verbose_name = _('Approval Limit')
        verbose_name_plural = _('Approval Limits')
        ordering = ['min_amount']

    def __str__(self):
        return f'{self.min_amount} - {self.max_amount or "∞"} -> {self.required_role}'


class AuditLog(models.Model):
    """PRD 31 / 35 - audit_logs. Immutable record of every change."""

    company = models.ForeignKey(
        Company, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_logs'
    )
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    model_name = models.CharField(max_length=100)
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=20)
    field_name = models.CharField(max_length=100, blank=True)
    previous_value = models.TextField(blank=True)
    new_value = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _('Audit Log')
        verbose_name_plural = _('Audit Logs')
        ordering = ['-timestamp']

    def __str__(self):
        return f'{self.model_name}#{self.object_id} {self.action}'


class PageSetting(TimeStampedModel):
    """Branding global untuk halaman admin/login/report."""

    class Language(models.TextChoices):
        INDONESIAN = 'id', _('Bahasa Indonesia')
        ENGLISH = 'en', _('English')

    title = models.CharField(max_length=100, default='Accounting & Finance')
    subtitle = models.CharField(max_length=255, blank=True, help_text='Tagline/sub judul di login atau halaman utama')
    logo = models.FileField(upload_to='branding/', blank=True)
    favicon = models.FileField(upload_to='branding/', blank=True)
    default_language = models.CharField(
        max_length=5, choices=Language.choices, default=Language.INDONESIAN,
        help_text='Bahasa default aplikasi'
    )

    class Meta:
        verbose_name = _('Page Setting')
        verbose_name_plural = _('Page Settings')

    def __str__(self):
        return self.title or 'Page Setting'

    @classmethod
    def current(cls):
        obj = cls.objects.order_by('id').first()
        if obj is None:
            obj = cls(title='Accounting & Finance')
        return obj

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old = PageSetting.objects.get(pk=self.pk)
                # delete old logo if changed
                if old.logo and self.logo and old.logo.name != self.logo.name and old.logo.name:
                    try:
                        old.logo.delete(save=False)
                    except Exception:
                        pass
                if old.favicon and self.favicon and old.favicon.name != self.favicon.name and old.favicon.name:
                    try:
                        old.favicon.delete(save=False)
                    except Exception:
                        pass
            except PageSetting.DoesNotExist:
                pass
        super().save(*args, **kwargs)

from .project_models import (  # noqa: E402,F401
    Project, ProjectBilling, ProjectCost, ProjectMilestone,
)

from .budget_models import Budget, BudgetStatus, BudgetType  # noqa: E402,F401

from .finance_models import Product, TaxCode  # noqa: E402,F401
