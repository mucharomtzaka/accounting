import os

from django.conf import settings

from .threadlocal import get_request, set_request
from .models import Company, PageSetting

BASE_DIR = settings.BASE_DIR
_ADMIN_CSS_FILES = ('css/admin-nav.css',)
# CSS report dibangun Tailwind CLI ke static/css/dist/. Dimitasi mtime seperti
# skin admin, karena sebelumnya dimuat tanpa ?v= sehingga browser menahan file
# lama walau sudah di-rebuild -> chart hilang saat refresh.
_REPORT_CSS_FILES = ('css/dist/styles.css',)


def _mtime_version(rel_paths):
    versions = []
    for rel in rel_paths:
        path = os.path.join(BASE_DIR, 'static', rel)
        try:
            versions.append(int(os.path.getmtime(path)))
        except OSError:
            versions.append(0)
    return max(versions) if versions else 0


def admin_css_version(request):
    """mtime file CSS admin sebagai cache-buster, supaya edit langsung ter-refresh."""
    version = _mtime_version(_ADMIN_CSS_FILES)
    report_version = _mtime_version(_REPORT_CSS_FILES)
    return {
        'admin_css_v': version,
        # Sprite icon app list - di-version sama supaya tak basi di cache.
        'app_icons_url': f'{settings.STATIC_URL}icons/apps.svg?v={version}',
        'report_css_url': f'{settings.STATIC_URL}css/dist/styles.css?v={report_version}',
    }


def current_company(request):
    if request.user.is_superuser:
        return Company.objects.filter(status='ACTIVE').first()
    access = request.user.user_access.filter(is_active=True).select_related('company').first()
    return access.company if access else None


def company_context(request):
    company = None
    if request.user.is_authenticated:
        qs = request.user.allowed_companies()
        available = qs
        cid = request.session.get('active_company_id')
        company = qs.filter(pk=cid).first() if cid else qs.first()
    return {
        'active_company': company,
        'available_companies': available if request.user.is_authenticated else Company.objects.none(),
        'switch_company_url': '/admin/core/company/switch/',
        # Report view render manual tanpa each_context AdminSite,
        # jadi branding diambil dari PageSetting bila ada, fallback ke settings.
        'page_setting': PageSetting.objects.order_by('id').first() or PageSetting(title=settings.ADMIN_SITE_HEADER),
        'admin_site_header': (PageSetting.objects.order_by('id').values_list('title', flat=True).first() or settings.ADMIN_SITE_HEADER),
        'admin_site_title': (PageSetting.objects.order_by('id').values_list('title', flat=True).first() or settings.ADMIN_SITE_TITLE),
    }