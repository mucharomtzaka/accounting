from django.contrib import admin, messages
from django.utils import timezone

from .models import Notification
from .scanner import scan


from core.admin_mixins import ChangeOnlyActionsMixin
from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
@admin.register(Notification)
class NotificationAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['title', 'severity', 'category', 'status', 'company', 'source_repr', 'created_at']
    list_filter = ['severity', 'category', 'status', 'company']
    search_fields = ['title', 'body', 'source_repr']
    actions = ['acknowledge', 'resolve', 'rescan']
    mutating_actions = ['acknowledge', 'resolve', 'rescan']
    readonly_fields = ['fingerprint', 'source_model', 'source_id', 'acknowledged_by', 'acknowledged_at']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs

    @admin.action(description='Acknowledge')
    def acknowledge(self, request, queryset):
        count = queryset.filter(status=Notification.Status.OPEN).update(
            status=Notification.Status.ACKNOWLEDGED,
            acknowledged_by=request.user, acknowledged_at=timezone.now(),
        )
        self.message_user(request, f'{count} notification di-acknowledge.')

    @admin.action(description='Resolve')
    def resolve(self, request, queryset):
        count = queryset.filter(status__in=[Notification.Status.OPEN,
                                            Notification.Status.ACKNOWLEDGED]).update(
            status=Notification.Status.RESOLVED,
            acknowledged_by=request.user, acknowledged_at=timezone.now(),
        )
        self.message_user(request, f'{count} notification di-resolve.')

    @admin.action(description='Scan ulang (buat notif baru)')
    def rescan(self, request, queryset):
        company_ids = {n.company_id for n in queryset}
        total = 0
        from core.models import Company
        for cid in company_ids:
            results = scan(Company.objects.get(pk=cid))
            total += sum(len(v) for k, v in results.items() if not k.endswith(':error'))
        self.message_user(request, f'Scan selesai: {total} notification baru.')