from django.contrib import admin, messages
from django.utils import timezone

from .models import (
    ConsolidationAdjustment, ConsolidationRun, IntercompanyTransaction, IntercompanyStatus,
)
from .services import eliminate, matching_exceptions, post_intercompany


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class ConsolidationAdjustmentInline(admin.TabularInline):
    model = ConsolidationAdjustment
    extra = 1


@admin.register(IntercompanyTransaction)
class IntercompanyTransactionAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['transaction_number', 'transaction_date', 'source_company',
                    'target_company', 'tx_type', 'amount', 'currency', 'status', 'eliminated_in']
    list_filter = ['source_company', 'target_company', 'tx_type', 'status', 'transaction_date']
    search_fields = ['transaction_number', 'source_document', 'description']
    actions = ['post_to_gl', 'show_matching_exceptions']
    mutating_actions = ['post_to_gl']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        qs = qs.filter(source_company__in=request.user.allowed_companies(request))
        return qs

    @admin.action(description='Post intercompany (Dr IC Receivable / Cr IC Revenue)')
    def post_to_gl(self, request, queryset):
        posted, errors = 0, []
        for tx in queryset:
            try:
                post_intercompany(tx, user=request.user)
                posted += 1
            except Exception as exc:
                errors.append(f'{tx.transaction_number}: {exc}')
        if posted:
            self.message_user(request, f'{posted} transaksi intercompany diposting.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)

    @admin.action(description='Cek matching (source = target)')
    def show_matching_exceptions(self, request, queryset):
        from core.models import Group

        groups = queryset.values_list('group', flat=True).distinct()
        for gid in groups:
            group = Group.objects.get(pk=gid)
            exceptions = matching_exceptions(group)
            if not exceptions:
                self.message_user(request, f'{group.name}: semua transaksi sudah match.')
            for exc in exceptions:
                self.message_user(request, f'{group.name}: {exc["message"]}',
                                  level=messages.WARNING)


@admin.register(ConsolidationRun)
class ConsolidationRunAdmin(ChangeOnlyActionsMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['run_name', 'group', 'period_from', 'period_to', 'status',
                    'eliminated_count', 'eliminated_amount', 'created_at']
    list_filter = ['group', 'status']
    inlines = [ConsolidationAdjustmentInline]
    actions = ['run_elimination', 'mark_approved']
    mutating_actions = ['run_elimination', 'mark_approved']

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            qs = qs.filter(group__companies__in=request.user.allowed_companies(request)).distinct()
        return qs

    @admin.action(description='Run elimination pada matched intercompany')
    def run_elimination(self, request, queryset):
        for run in queryset:
            try:
                count, total, exceptions = eliminate(run, user=request.user)
                msg = f'{run.run_name}: {count} transaksi dieliminasi, total {total}.'
                if exceptions:
                    msg += f' ({len(exceptions)} exception belum match).'
                self.message_user(request, msg)
            except Exception as exc:
                self.message_user(request, f'{run.run_name}: {exc}', level=messages.ERROR)

    @admin.action(description='Mark consolidation APPROVED')
    def mark_approved(self, request, queryset):
        queryset.update(status=ConsolidationRun.Status.APPROVED)
        self.message_user(request, f'{queryset.count()} run ditandai approved.')