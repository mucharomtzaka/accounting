import io
import os
import tempfile

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.i18n import set_language as django_set_language

from .forms import RegisterCompanyForm
from .models import Company, Group, UserCompanyAccess

User = get_user_model()


@require_http_methods(['GET', 'POST'])
def register_company(request):
    """Daftar company baru + user owner (role COMPANY_OWNER).

    Owner TIDAK diberi is_superuser: superuser melihat seluruh company
    (allowed_companies), itu pelanggaran Rule 1 company isolation.
    """
    if request.method == 'POST':
        form = RegisterCompanyForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                code = form.cleaned_data['company_code']

                # Group otomatis 1 company = 1 group (bisa digabung nanti).
                group, _ = Group.objects.get_or_create(
                    code=code,
                    defaults={'name': form.cleaned_data['legal_name']},
                )
                company = Company.objects.create(
                    group=group,
                    company_code=code,
                    legal_name=form.cleaned_data['legal_name'],
                    display_name=form.cleaned_data['display_name'],
                    npwp=form.cleaned_data['npwp'],
                    base_currency=form.cleaned_data['base_currency'],
                )

                user = User.objects.create_user(
                    username=form.cleaned_data['username'],
                    password=form.cleaned_data['password1'],
                    email=form.cleaned_data['email'],
                    first_name=form.cleaned_data['first_name'],
                    last_name=form.cleaned_data['last_name'],
                    role=User.Role.COMPANY_OWNER,
                    is_staff=True,
                    is_superuser=False,
                )
                UserCompanyAccess.objects.create(user=user, company=company)

                # Izin owner register = izin owner demo: satu sumber mapping di
                # seed_demo_users (semua model company-scoped + kelola user).
                # Dulu daftar SCOPED_MODELS sendiri di sini dan sudah basi: tidak
                # memuat model baru dan justru memberi model yang admin-nya TIDAK
                # company-scoped (bocor antar company).
                from .management.commands.seed_demo_users import sync_role_group

                user.groups.add(sync_role_group(company, User.Role.COMPANY_OWNER))

            # COA supaya company bisa langsung dipakai.
            from .coa_template import seed_coa
            seed_coa(company)

            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(request, f'Company {code} dibuat. Selamat datang!')
            return redirect(reverse('admin:index'))
    else:
        form = RegisterCompanyForm()

    return render(request, 'core/register_company.html', {'form': form})


def switch_company(request):
    """Simpan company aktif di session, redirect kembali."""
    company_id = request.GET.get('company_id')
    if company_id and request.user.allowed_companies().filter(pk=company_id).exists():
        request.session['active_company_id'] = company_id
    else:
        request.session.pop('active_company_id', None)
    return redirect(request.META.get('HTTP_REFERER') or reverse('admin:index'))


def _strip_language_prefix(path):
    """Buang prefix /<kode>/ di awal path, mis. /en/admin/ -> /admin/.

    Django reverse() menolak path berawalan bahasa yang tak sama dengan bahasa
    aktif, jadi prefix harus dibuang sebelum diterjemahkan.
    """
    for code, _name in settings.LANGUAGES:
        for prefix in (f'/{code}', f'/{code}/'):
            if path.startswith(prefix):
                return path[len(prefix):] or '/'
    return path


def set_language(request):
    """Ganti bahasa, lalu arahkan ke URL yang sama TANPA prefix bahasa lama.

    View bawaan Django butuh prefix `next` cocok dengan bahasa aktif; karena
    LocalePrefixPattern hanya mencocokkan prefix bahasa aktif, `next=/en/admin/`
    gagal di-resolve saat pindah ke `id` sehingga redirect buntu.
    """
    if request.method == 'POST':
        next_url = request.POST.get('next') or ''
        if next_url and next_url.startswith('/'):
            cleaned = _strip_language_prefix(next_url.split('?')[0])
            query = next_url.split('?')[1] if '?' in next_url else ''
            next_url = cleaned + (f'?{query}' if query else '')
            post = request.POST.copy()
            post['next'] = next_url
            request.POST = post
    return django_set_language(request)


def _superuser_required(request):
    if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
        raise PermissionDenied


def database_backup(request):
    """Halaman backup & restore, khusus superuser.

    Download pakai dumpdata (tanpa contenttypes/Permission, dibuat ulang oleh
    migrate). Restore pakai loaddata dari file JSON hasil download.
    """
    _superuser_required(request)
    if request.method == 'POST':
        upload = request.FILES.get('backup_file')
        if not upload or not upload.name.endswith('.json'):
            messages.error(request, 'Pilih file backup .json hasil download.')
            return redirect(reverse('core_database_backup'))
        if upload.size > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
            messages.error(request, 'File terlalu besar (maks 20 MB).')
            return redirect(reverse('core_database_backup'))
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as tmp:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
        try:
            call_command('loaddata', tmp_path, verbosity=0)
        except Exception as exc:
            messages.error(request, f'Restore gagal: {exc}')
        else:
            messages.success(request, 'Restore berhasil.')
        finally:
            os.unlink(tmp_path)
        return redirect(reverse('core_database_backup'))
    return render(request, 'core/database_backup.html')


def database_download(request):
    """Download JSON dumpdata, khusus superuser."""
    _superuser_required(request)
    buf = io.StringIO()
    call_command('dumpdata', '--natural-foreign', '--natural-primary',
                 '-e', 'contenttypes', '-e', 'auth.Permission',
                 '--indent', '2', stdout=buf)
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    response = HttpResponse(buf.getvalue(), content_type='application/json')
    response['Content-Disposition'] = f'attachment; filename="backup-{stamp}.json"'
    return response