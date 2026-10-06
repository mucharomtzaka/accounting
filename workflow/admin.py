from django.contrib import admin, messages

from .models import ApprovalRequest, ApprovalStep, ApprovalWorkflow
from .services import approve, reject


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class ApprovalStepInline(admin.TabularInline):
    model = ApprovalStep
    extra = 1


@admin.register(ApprovalWorkflow)
class ApprovalWorkflowAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['name', 'company', 'model_name', 'is_active']
    list_filter = ['company', 'model_name', 'is_active']
    search_fields = ['name', 'model_name']
    inlines = [ApprovalStepInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs


@admin.register(ApprovalRequest)
class ApprovalRequestAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['object_repr', 'model_name', 'company', 'step', 'requester',
                    'amount', 'status', 'created_at']
    list_filter = ['status', 'workflow__company', 'step__required_role']
    search_fields = ['object_repr', 'object_id', 'requester__username']
    readonly_fields = ['workflow', 'step', 'requester', 'model_name', 'object_id',
                       'object_repr', 'amount', 'currency']
    actions = ['approve_request', 'reject_request']
    mutating_actions = ['approve_request', 'reject_request']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(workflow__company__in=request.user.allowed_companies(request))
        return qs

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'approver':
            from django.db.models import Q

            from core.models import User

            allowed = request.user.allowed_companies(request)
            kwargs['queryset'] = (
                User.objects.filter(is_active=True, is_staff=True)
                .filter(
                    Q(is_superuser=True)
                    | Q(user_access__company__in=allowed, user_access__is_active=True)
                )
                .distinct()
                .order_by('username')
            )
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    @admin.display(description='Company')
    def company(self, obj):
        return obj.workflow.company

    @admin.action(description='Approve request terpilih')
    def approve_request(self, request, queryset):
        done, errors = 0, []
        for req in queryset:
            try:
                approve(req, request.user)
                done += 1
            except Exception as exc:
                errors.append(f'{req.object_repr}: {exc}')
        if done:
            self.message_user(request, f'{done} request disetujui.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)

    @admin.action(description='Reject request terpilih')
    def reject_request(self, request, queryset):
        done, errors = 0, []
        for req in queryset:
            try:
                reject(req, request.user, reason='Rejected via admin')
                done += 1
            except Exception as exc:
                errors.append(f'{req.object_repr}: {exc}')
        if done:
            self.message_user(request, f'{done} request ditolak.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)