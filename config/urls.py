from django.conf import settings
from django.conf.urls.i18n import i18n_patterns
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.urls import include, path
from django.utils.translation import gettext_lazy as _
import types

from core import views as core_views
from django.shortcuts import redirect
from reports import views as report_views

admin_site = admin.site
admin_site.site_header = settings.ADMIN_SITE_HEADER
admin_site.site_title = settings.ADMIN_SITE_TITLE
admin_site.index_title = settings.ADMIN_INDEX_TITLE
# Tanpa ini userlinks merender "View site" yang hanya mengarahkan ke root site.
admin_site.site_url = None
admin_view = admin_site.admin_view


def _get_app_list(self, request, app_label=None, *args, **kwargs):
    """Pisahkan PageSetting dari kartu Core; tampilkan sebagai menu sendiri di index."""
    app_list = AdminSite.get_app_list(self, request, app_label, *args, **kwargs)
    if request.user.is_superuser:
        for app in app_list:
            if app.get('app_label') != 'core':
                continue
            page_models = [m for m in app.get('models', [])
                           if str(m.get('admin_url', '')).endswith('/core/pagesetting/')]
            if page_models:
                app['models'] = [m for m in app['models'] if m not in page_models]
                if not app_label:
                    app_list.insert(app_list.index(app) + 1, {
                        **app,
                        'name': page_models[0]['name'],
                        'app_url': page_models[0]['admin_url'],
                        'models': page_models,
                    })
            break
    return app_list


admin_site.get_app_list = types.MethodType(_get_app_list, admin_site)

# URL report didaftarkan SEBELUM admin.site.urls karena catch-all admin
# akan menelan semua path di bawah /admin/.
# i18n_patterns membungkus prefix bahasa, jadi settings.LANGUAGES (id/en) bisa
# dipilih user lewat header dan konten statis tetap ikut bahasa aktif.
urlpatterns = [
    path('i18n/setlang/', core_views.set_language, name='set_language'),
    path('i18n/', include('django.conf.urls.i18n')),
]

urlpatterns += i18n_patterns(
    path('', lambda request: redirect('admin:index')),
    # Dashboard berdiri sendiri, tidak lagi satu butir di bawah /admin/reports/.
    # Nama URL tetap 'reports_dashboard' supaya smoke test & template lain
    # tidak ikut berubah; path lama /admin/reports/ cuma redirect.
    path('admin/dashboard/', admin_view(report_views.dashboard), name='reports_dashboard'),
    path('admin/reports/', lambda request: redirect('reports_dashboard')),
    path('admin/reports/trial-balance/', admin_view(report_views.trial_balance_report),
         name='reports_trial_balance'),
    path('admin/reports/profit-loss/', admin_view(report_views.profit_loss_report),
         name='reports_profit_loss'),
    path('admin/reports/balance-sheet/', admin_view(report_views.balance_sheet_report),
         name='reports_balance_sheet'),
    path('admin/reports/cash-flow/', admin_view(report_views.cash_flow_report),
         name='reports_cash_flow'),
    path('admin/reports/general-ledger/', admin_view(report_views.general_ledger_report),
         name='reports_general_ledger'),
    path('admin/reports/balance-comparison/', admin_view(report_views.balance_comparison_report),
         name='reports_balance_comparison'),
    path('admin/reports/aging/', admin_view(report_views.aging_report), name='reports_aging'),
    path('admin/reports/company-comparison/', admin_view(report_views.company_comparison),
         name='reports_company_comparison'),
    path('admin/reports/consolidated/', admin_view(report_views.consolidated_report),
         name='reports_consolidated'),
    path('admin/reports/project-profitability/', admin_view(report_views.project_profitability_report),
         name='reports_project_profitability'),
    path('admin/reports/budget-vs-actual/', admin_view(report_views.budget_vs_actual_report),
         name='reports_budget_vs_actual'),
    path('admin/reports/cost-accounting/', admin_view(report_views.cost_accounting_report),
         name='reports_cost_accounting'),
    path('admin/reports/product-profitability/', admin_view(report_views.product_profitability_report),
         name='reports_product_profitability'),
    path('admin/reports/customer-statement/', admin_view(report_views.customer_statement_report),
         name='reports_customer_statement'),
    path('admin/reports/vendor-statement/', admin_view(report_views.vendor_statement_report),
         name='reports_vendor_statement'),
    path('admin/core/company/switch/', admin_view(core_views.switch_company), name='core_switch_company'),
    path('admin/database-backup/', admin_view(core_views.database_backup), name='core_database_backup'),
    path('admin/database-backup/download/', admin_view(core_views.database_download), name='core_database_download'),
    path('register/', core_views.register_company, name='core_register_company'),
    path('admin/', admin_site.urls),
    prefix_default_language=False,
)

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)