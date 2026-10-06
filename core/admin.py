from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group as AuthGroup
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import (
    ApprovalLimit, AuditLog, Branch, Company, CostCenter, Currency, Department,
    ExchangeRate, Group, PageSetting, Product, TaxCode, User, UserBranchAccess, UserCompanyAccess,
)
from .budget_models import Budget, BudgetStatus, BudgetType
from .project_models import Project, ProjectBilling, ProjectCost, ProjectMilestone


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class UserCompanyAccessInline(admin.TabularInline):
    model = UserCompanyAccess
    extra = 1


class UserBranchAccessInline(admin.TabularInline):
    model = UserBranchAccess
    extra = 1


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Turun dari UserAdmin bawaan Django, BUKAN ModelAdmin.

    Dengan ModelAdmin, field `password` jadi CharField biasa: mengetik "demo123"
    di form tambah/ubah menyimpan teks itu apa adanya ke DB. `check_password()`
    selalu gagal karena kolomnya bukan hash, user pun tidak bisa login walau
    password-nya "sudah diisi". UserAdmin bawaan yang menyediakan
    UserCreationForm (add: password1/password2) + ReadOnlyPasswordHashField
    (change: hash tidak bisa ditimpa, ganti lewat Change password).
    """

    list_display = ['username', 'get_full_name', 'email', 'role', 'companies_count', 'is_active']
    list_filter = ['role', 'is_active', 'is_staff']
    search_fields = ['username', 'first_name', 'last_name', 'email']
    inlines = [UserCompanyAccessInline, UserBranchAccessInline]
    filter_horizontal = ('groups', 'user_permissions')
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal', {'fields': ('first_name', 'last_name', 'email')}),
        ('Role & Access', {'fields': ('role', 'is_active', 'is_staff', 'is_superuser')}),
        ('Permissions', {'fields': ('groups', 'user_permissions')}),
        ('Dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {'classes': ('wide',),
                'fields': ('username', 'password1', 'password2', 'role', 'is_staff')}),
    )

    @admin.display(description='Companies')
    def companies_count(self, obj):
        return obj.user_access.count()

    def get_queryset(self, request):
        """Company isolation (Rule 1): user company-scoped hanya boleh melihat
        user yang punya akses ke company yang boleh ia akses, plus dirinya
        sendiri. Tanpa ini, begitu `core.User` diberikan ke role demo lewat
        seed_demo_users, satu user company A bisa melihat seluruh user semua
        company. Superuser tetap lihat semua."""
        qs = super().get_queryset(request).prefetch_related('user_access')
        if request.user.is_superuser:
            return qs
        companies = request.user.allowed_companies(request)
        return qs.filter(
            Q(user_access__company__in=companies) | Q(pk=request.user.pk)
        ).distinct()


@admin.register(Company)
class CompanyAdmin(CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['company_code', 'display_name', 'group', 'base_currency', 'ownership_percentage', 'status']
    list_filter = ['group', 'status', 'base_currency']
    search_fields = ['company_code', 'legal_name', 'display_name', 'npwp']
    fieldsets = (
        ('Identity', {'fields': ('group', 'company_code', 'legal_name', 'display_name')}),
        ('Tax & Legal', {'fields': ('npwp', 'nib', 'tax_registration')}),
        ('Address', {'fields': ('address', 'city', 'province', 'country', 'phone', 'email', 'website')}),
        ('Accounting', {'fields': ('base_currency', 'fiscal_year_start', 'ownership_percentage', 'status')}),
    )


class DepartmentInline(admin.TabularInline):
    model = Department
    extra = 0
    fields = ['department_code', 'department_name', 'status']


@admin.register(Group)
class GroupAdmin(CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['code', 'name', 'companies_count', 'is_active']

    @admin.display(description='Companies')
    def companies_count(self, obj):
        return obj.companies.count()


admin.site.unregister(AuthGroup)


@admin.register(AuthGroup)
class AuthGroupAdmin(DjangoGroupAdmin):
    """auth.Group dengan company isolation.

    Group izin dibuat per company dengan pola nama "<Role> - <KODE>" (lihat
    seed_demo_users.sync_role_group), tapi modelnya sendiri tidak punya FK
    company. Scope diturunkan dari kode company yang boleh diakses user, sama
    seperti changelist lain (allowed_companies(request) ikut company aktif).
    """

    list_display = ['name', 'permissions_count']
    search_fields = ['name']

    @admin.display(description='Permissions')
    def permissions_count(self, obj):
        return obj.permissions.count()

    def get_queryset(self, request):
        qs = super().get_queryset(request).prefetch_related('permissions')
        # Satu company aktif saja - sama seperti core.context_processors.company_context
        # (session `active_company_id`, kalau kosong ambil company pertama yang
        # boleh diakses). Tanpa ini superuser melihat grup seluruh company
        # sekaligus walau header sudah menunjuk satu company.
        companies = request.user.allowed_companies(request)
        cid = request.session.get('active_company_id')
        active = companies.filter(pk=cid).first() if cid else companies.first()
        if active is None:
            return qs.none()
        return qs.filter(name__endswith=f' - {active.company_code}')


@admin.register(Branch)
class BranchAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['branch_code', 'branch_name', 'company', 'manager', 'default_warehouse', 'status']
    list_filter = ['company', 'status']
    search_fields = ['branch_code', 'branch_name']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))


@admin.register(Department)
class DepartmentAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['department_code', 'department_name', 'company', 'status']
    list_filter = ['company', 'status']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))


@admin.register(CostCenter)
class CostCenterAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['cost_center_code', 'cost_center_name', 'company', 'department', 'branch', 'status']
    list_filter = ['company', 'status']
    search_fields = ['cost_center_code', 'cost_center_name']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))


@admin.register(Currency)
class CurrencyAdmin(CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['code', 'name', 'symbol', 'is_active']


@admin.register(Product)
class ProductAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['product_code', 'product_name', 'company', 'product_type', 'uom',
                    'price', 'cost', 'is_active']
    list_filter = ['company', 'product_type', 'is_active']
    search_fields = ['product_code', 'product_name']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(TaxCode)
class TaxCodeAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['tax_code', 'name', 'company', 'tax_type', 'rate', 'tax_account', 'is_active']
    list_filter = ['company', 'tax_type', 'is_active']
    search_fields = ['tax_code', 'name']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(Project)
class ProjectAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['project_code', 'project_name', 'company', 'branch', 'customer',
                    'project_type', 'contract_value', 'budget_cost', 'status']
    list_filter = ['company', 'project_type', 'status']
    search_fields = ['project_code', 'project_name']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))


@admin.register(ExchangeRate)
class ExchangeRateAdmin(CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['currency', 'company', 'rate_date', 'rate_fmt']
    list_filter = ['currency', 'rate_date']

    def get_queryset(self, request):
        # Kurs global (company kosong) tetap terlihat: dipakai sebagai fallback.
        return super().get_queryset(request).filter(
            Q(company__in=request.user.allowed_companies(request)) | Q(company__isnull=True))

    @admin.display(description='Rate')
    def rate_fmt(self, obj):
        from decimal import Decimal
        v = obj.rate
        if v is None:
            return '-'
        try:
            q = v.quantize(Decimal('0.00000001')).normalize()
            s = format(q, 'f')
            if '.' in s:
                s = s.rstrip('0').rstrip('.')
            return s
        except Exception:
            return str(v)


@admin.register(ApprovalLimit)
class ApprovalLimitAdmin(CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['company', 'min_amount', 'max_amount', 'required_role', 'currency']
    list_filter = ['company', 'required_role', 'currency']
    search_fields = ['company__company_code', 'required_role']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))

    def formfield_for_choice_field(self, db_field, request, **kwargs):
        if db_field.name == 'required_role':
            from core.models import User
            from workflow.models import ApprovalStep

            allowed = request.user.allowed_companies(request)
            # ApprovalLimit seharusnya hanya memakai role yang benar-benar punya
            # step di workflow perusahaan ini. Tanpa langkah itu, dropdown terisi
            # semua role demo/user dan limit bisa diset untuk role tak pernah dipakai.
            role_codes = set(
                ApprovalStep.objects.filter(workflow__company__in=allowed)
                .values_list('required_role', flat=True)
            )
            choices = [choice for choice in User.Role.choices if choice[0] in role_codes]
            if choices:
                kwargs['choices'] = choices
        return super().formfield_for_choice_field(db_field, request, **kwargs)

    def get_form(self, request, obj=None, change=False, **kwargs):
        form = super().get_form(request, obj=obj, change=change, **kwargs)
        if obj and obj.required_role:
            choices = list(form.base_fields['required_role'].choices)
            if not any(code == obj.required_role for code, _label in choices):
                from core.models import User
                label = dict(User.Role.choices).get(obj.required_role, obj.required_role)
                choices.append((obj.required_role, label))
                form.base_fields['required_role'].choices = choices
        return form


@admin.register(PageSetting)
class PageSettingAdmin(admin.ModelAdmin):
    list_display = ['title', 'subtitle', 'logo', 'favicon']
    fieldsets = (
        ('Branding', {'fields': ('title', 'subtitle', 'logo', 'favicon', 'default_language')}),
    )

    def has_module_permission(self, request):
        return request.user.is_superuser

    def get_model_perms(self, request):
        if not request.user.is_superuser:
            return {'add': False, 'change': False, 'delete': False, 'view': False}
        return super().get_model_perms(request)

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser or super().has_view_permission(request, obj)

    def has_add_permission(self, request):
        return request.user.is_superuser and not PageSetting.objects.exists()

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser or super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditLog)
class AuditLogAdmin(CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['timestamp', 'model_name', 'object_id', 'action', 'field_name', 'previous_value', 'new_value', 'user']
    list_filter = ['model_name', 'action', 'timestamp']
    search_fields = ['object_id', 'user__username']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

class ProjectCostInline(admin.TabularInline):
    model = ProjectCost
    extra = 0
    fields = ['cost_date', 'cost_type', 'description', 'quantity', 'unit_price', 'total_amount', 'is_posted']
    readonly_fields = ['total_amount', 'is_posted']
    extra = 0


class ProjectMilestoneInline(admin.TabularInline):
    model = ProjectMilestone
    extra = 0


class ProjectBillingInline(admin.TabularInline):
    model = ProjectBilling
    extra = 0
    fields = ['billing_number', 'billing_date', 'milestone', 'gross_amount',
              'retention_percent', 'retention_amount', 'net_amount', 'status']
    readonly_fields = ['retention_amount', 'net_amount', 'status']


@admin.register(ProjectCost)
class ProjectCostAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['project', 'cost_date', 'cost_type', 'description', 'total_amount', 'is_posted']
    list_filter = ['project__company', 'cost_type', 'is_posted']
    search_fields = ['description', 'project__project_code']
    actions = ['post_to_gl']
    mutating_actions = ['post_to_gl']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(project__company__in=request.user.allowed_companies(request))
        return qs

    @admin.action(description='Post ke GL (Dr WIP / Cr AP)')
    def post_to_gl(self, request, queryset):
        from core.project_services import post_project_cost

        posted, errors = 0, []
        for cost in queryset:
            try:
                post_project_cost(cost, user=request.user)
                posted += 1
            except Exception as exc:
                errors.append(f'{cost.project.project_code}/{cost.pk}: {exc}')
        if posted:
            self.message_user(request, f'{posted} project cost diposting.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)


@admin.register(ProjectMilestone)
class ProjectMilestoneAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['project', 'milestone_code', 'milestone_name', 'target_date',
                    'percent_complete', 'billable_amount', 'billed_amount', 'is_billed']
    list_filter = ['project__company', 'is_billed']
    search_fields = ['milestone_code', 'milestone_name']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(project__company__in=request.user.allowed_companies(request))
        return qs


@admin.register(ProjectBilling)
class ProjectBillingAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['project', 'billing_number', 'billing_date', 'milestone',
                    'gross_amount', 'retention_amount', 'net_amount', 'status']
    list_filter = ['project__company', 'status']
    search_fields = ['billing_number', 'project__project_code']
    actions = ['post_to_gl']
    mutating_actions = ['post_to_gl']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(project__company__in=request.user.allowed_companies(request))
        return qs

    @admin.action(description='Post ke GL (Dr AR / Cr Revenue)')
    def post_to_gl(self, request, queryset):
        from core.project_services import post_project_billing

        posted, errors = 0, []
        for billing in queryset:
            try:
                post_project_billing(billing, user=request.user)
                posted += 1
            except Exception as exc:
                errors.append(f'{billing.project.project_code}/{billing.billing_number}: {exc}')
        if posted:
            self.message_user(request, f'{posted} project billing diposting.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)


@admin.register(Budget)
class BudgetAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['company', 'fiscal_year', 'period', 'account', 'amount', 'revision', 'status']
    list_filter = ['company', 'fiscal_year', 'budget_type', 'status']
    search_fields = ['account__account_code', 'account__account_name']
    actions = ['submit_budget', 'approve_budget', 'reject_budget']
    mutating_actions = ['submit_budget', 'approve_budget', 'reject_budget']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs

    @admin.action(description='Submit budget')
    def submit_budget(self, request, queryset):
        updated = queryset.filter(status=BudgetStatus.DRAFT).update(status=BudgetStatus.SUBMITTED)
        self.message_user(request, f'{updated} budget disubmit.')

    @admin.action(description='Approve budget')
    def approve_budget(self, request, queryset):
        updated = queryset.filter(status=BudgetStatus.SUBMITTED).update(status=BudgetStatus.APPROVED)
        self.message_user(request, f'{updated} budget disetujui.')

    @admin.action(description='Reject budget')
    def reject_budget(self, request, queryset):
        updated = queryset.filter(status=BudgetStatus.SUBMITTED).update(status=BudgetStatus.REJECTED)
        self.message_user(request, f'{updated} budget ditolak.')
