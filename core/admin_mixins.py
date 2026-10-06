"""Mixin permission untuk ModelAdmin.

Django menampilkan `actions` ke siapa pun yang bisa membuka changelist (cukup
permission view), jadi action yang menulis data tetap bisa dipicu user
read-only. Mixin ini menyembunyikan action mutasi kalau user tidak punya
permission change - sejalan dengan add/change form yang memang sudah dijaga
Django lewat has_add_permission / has_change_permission.
"""
from django.contrib import admin as django_admin


class ChangeOnlyActionsMixin:
    #: Nama action yang mengubah data. Action yang hanya membaca/mengunduh
    #: (mis. export CSV, show exceptions) tidak perlu masuk daftar ini.
    mutating_actions = ()

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self.has_change_permission(request):
            for name in self.mutating_actions:
                actions.pop(name, None)
        return actions


class CompanyScopedFilter(django_admin.RelatedFieldListFilter):
    """Filter sidebar untuk FK company: hanya company yang boleh diakses user.

    Django membangun pilihan filter FK dari `_default_manager` model relasi, jadi
    walau changelist sudah difilter ke `allowed_companies`, sidebar tetap
    menampilkan seluruh company - dan memilih company lain menghasilkan hasil
    kosong. Superuser tetap melihat semua (allowed_companies mengembalikan semua).
    """

    def field_choices(self, field, request, model_admin):
        companies = request.user.allowed_companies(request)
        return [(company.pk, str(company)) for company in companies]


class CompanyScopedFiltersMixin:
    """Ganti otomatis semua filter sidebar yang berakhiran `company`.

    Mencakup path seperti `company`, `project__company`, `inventory__company`,
    `journal__company`, `source_company`, `target_company` - semuanya FK ke
    Company, jadi pilihannya cukup diambil dari allowed_companies.
    """

    def get_list_filter(self, request):
        filters = list(super().get_list_filter(request))
        return [
            (item, CompanyScopedFilter)
            if isinstance(item, str) and item.endswith('company')
            else item
            for item in filters
        ]


class CompanyScopedFormMixin:
    """Batasi pilihan field FK ke Company pada form add/change.
    Batasi juga FK yang berkaitan dengan company (product, warehouse, branch, cost_center, tax_code, customer, supplier, project, department, asset_category, bank_account dll) agar user hanya bisa memilih data milik company yang diizinkan.
    Untuk audit fields (created_by/updated_by) batasi ke user yang memiliki akses ke company user.
    Superuser tidak dibatasi (allowed_companies mengembalikan semua).
    """

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        from core.models import Company, User
        remote = getattr(db_field, 'remote_field', None)
        if remote is not None:
            model = remote.model
            if model is Company:
                kwargs['queryset'] = request.user.allowed_companies(request)
                return super().formfield_for_foreignkey(db_field, request, **kwargs)
            if model is User and db_field.name in ('created_by', 'updated_by'):
                comps = request.user.allowed_companies(request)
                if getattr(request.user, 'is_superuser', False):
                    kwargs['queryset'] = User.objects.all()
                else:
                    kwargs['queryset'] = User.objects.filter(
                        user_access__company__in=comps
                    ).distinct()
                return super().formfield_for_foreignkey(db_field, request, **kwargs)
            opts = getattr(model, '_meta', None)
            if opts is not None:
                field_names = [f.name for f in opts.fields]
                if 'company' in field_names:
                    kwargs['queryset'] = model.objects.filter(
                        company__in=request.user.allowed_companies(request)
                    )
                    return super().formfield_for_foreignkey(db_field, request, **kwargs)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)
