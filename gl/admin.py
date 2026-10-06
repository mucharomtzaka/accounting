from datetime import datetime
from decimal import Decimal

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy

from . import csv_io
from .engine import AccountingError, PeriodLockedError, UnbalancedJournalError, reverse_journal
from .models import (
    Account, AccountCategory, AccountMapping, AccountingPeriod, FiscalYear,
    JournalEntry, JournalLine, JournalStatus, JournalTemplate, JournalTemplateLine,
)
from .services import close_period, reopen_period


from core.admin_mixins import ChangeOnlyActionsMixin


from core.admin_mixins import CompanyScopedFiltersMixin
from core.admin_mixins import CompanyScopedFormMixin
class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 1
    fields = ['line_no', 'account', 'description', 'debit', 'credit', 'customer', 'supplier', 'project']
    autocomplete_fields = ['account']

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('account')


@admin.register(Account)
class AccountAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = [
        'account_code', 'account_name', 'account_type', 'company',
        'normal_balance', 'is_control_account', 'is_postable', 'is_active',
    ]
    list_filter = ['company', 'account_category', 'normal_balance', 'is_active', 'is_postable']
    search_fields = ['account_code', 'account_name']
    autocomplete_fields = ['parent']
    ordering = ['company', 'account_code']
    actions = ['deactivate', 'export_accounts_csv']
    mutating_actions = ['deactivate']
    change_list_template = 'admin/gl/account/change_list.html'
    csv_session_key = 'gl_account_coa_import_rows'

    def get_queryset(self, request):
        return (super().get_queryset(request)
                .select_related('company', 'parent', 'currency')
                .filter(company__in=request.user.allowed_companies(request)))

    def get_urls(self):
        # URL custom diletakkan sebelum super() supaya tidak tertelan
        # regex URL bawaan admin (mis. <path>/delete/).
        return [
            path('import-csv/', self.admin_site.admin_view(self.import_csv_view),
                 name='gl_account_import_csv'),
            path('export-csv/template/', self.admin_site.admin_view(self.template_csv_view),
                 name='gl_account_template_csv'),
            path('export-csv/', self.admin_site.admin_view(self.export_csv_view),
                 name='gl_account_export_csv'),
        ] + super().get_urls()

    # ---------- export ----------

    def _csv_response(self, filename, rows):
        response = HttpResponse(csv_io.render_csv(rows), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    @admin.action(description='Export akun terpilih ke CSV')
    def export_accounts_csv(self, request, queryset):
        stamp = datetime.now().strftime('%Y%m%d-%H%M')
        accounts = queryset.select_related('parent', 'currency')
        return self._csv_response(f'chart-of-accounts-{stamp}.csv', csv_io.export_rows(accounts))

    def template_csv_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        return self._csv_response('chart-of-accounts-template.csv', csv_io.template_rows())

    def export_csv_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        company = self._pick_company(request)
        if company is None:
            self.message_user(request, 'Pilih company dulu untuk export akun.', level=messages.WARNING)
            return HttpResponse(status=302, headers={'Location': reverse('admin:gl_account_changelist')})
        accounts = (Account.objects.filter(company=company)
                    .select_related('parent', 'currency').order_by('account_code'))
        stamp = datetime.now().strftime('%Y%m%d-%H%M')
        return self._csv_response(
            f'chart-of-accounts-{company.company_code}-{stamp}.csv', csv_io.export_rows(accounts))

    # ---------- import ----------

    def _pick_company(self, request):
        """Company tujuan import: dari form kalau ada, kalau tidak company aktif."""
        allowed = request.user.allowed_companies()
        requested = request.POST.get('company_id') or request.GET.get('company_id')
        if requested:
            picked = allowed.filter(pk=requested).first()
            if picked is None:
                raise PermissionDenied('Company tujuan tidak ada di daftar yang boleh diakses.')
            return picked
        return request.user.allowed_companies(request).first()

    def import_csv_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        can_import = self.has_add_permission(request)
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Import / Export Chart of Accounts',
            'companies': request.user.allowed_companies(),
            'company': None,
            'can_import': can_import,
            'plan': None,
            'errors': [],
            'summary': '',
        }

        if request.method != 'POST':
            context['company'] = self._pick_company(request)
            return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)

        if not can_import:
            raise PermissionDenied('Hak tambah akun diperlukan untuk import.')

        company = self._pick_company(request)
        context['company'] = company

        if request.POST.get('confirm'):
            rows = request.session.pop(self.csv_session_key, None)
            if not rows:
                self.message_user(request, 'Sesi preview habis, upload ulang file CSV.', level=messages.WARNING)
                return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)
            plan = csv_io.build_plan(rows, company)
            if plan['errors']:
                context['errors'] = plan['errors']
                return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)
            result = csv_io.commit_plan(plan, company, request.user)
            self.message_user(
                request,
                f'{company.company_code}: {result["created"]} akun dibuat, '
                f'{result["updated"]} diupdate, {result["unchanged"]} tidak berubah.',
            )
            return HttpResponse(status=302, headers={'Location': reverse('admin:gl_account_changelist')})

        uploaded = request.FILES.get('csv_file')
        if uploaded is None:
            self.message_user(request, 'Tidak ada file CSV yang dipilih.', level=messages.ERROR)
            return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)

        header_map, raw_rows, errors = csv_io.parse_account_csv(uploaded)
        if not errors:
            rows, errors = csv_io.validate_rows(header_map, raw_rows)
        else:
            rows = []

        if errors:
            context['errors'] = errors
            return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)

        if not rows:
            self.message_user(request, 'File CSV tidak berisi baris data.', level=messages.WARNING)
            return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)

        plan = csv_io.build_plan(rows, company)
        context['plan'] = plan
        context['errors'] = plan['errors']
        context['summary'] = csv_io.plan_summary(plan)
        if plan['errors']:
            request.session.pop(self.csv_session_key, None)
            return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)

        # Simpan baris mentah (bukan hasil plan) supaya confirm selalu
        # divalidasi ulang terhadap kondisi DB terbaru.
        request.session[self.csv_session_key] = rows
        return TemplateResponse(request, 'admin/gl/account/import_csv.html', context)

    @admin.action(description='Nonaktifkan akun terpilih')
    def deactivate(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} akun dinonaktifkan.')
        return updated


@admin.register(AccountMapping)
class AccountMappingAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['company', 'module', 'key', 'account', 'is_active']
    list_filter = ['company', 'module']
    autocomplete_fields = ['account']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))


class JournalTemplateLineInline(admin.TabularInline):
    model = JournalTemplateLine
    extra = 1
    autocomplete_fields = ['account']


@admin.register(JournalTemplate)
class JournalTemplateAdmin(CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['name', 'company', 'source', 'is_active']
    inlines = [JournalTemplateLineInline]

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))


@admin.register(FiscalYear)
class FiscalYearAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['name', 'company', 'start_date', 'end_date', 'is_closed', 'periods_count']
    list_filter = ['company', 'is_closed']
    actions = ['generate_periods', 'close_fiscal_year_action', 'reopen_fiscal_year_action']
    mutating_actions = ['generate_periods', 'close_fiscal_year_action', 'reopen_fiscal_year_action']
    # is_closed hanya boleh berubah lewat action tutup/buka buku, bukan dicentang
    # manual - kalau manual, tahun bisa "closed" tanpa jurnal penutup & jejak audit.
    readonly_fields = ['is_closed', 'closed_by', 'closed_at']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))

    @admin.display(description='Periods')
    def periods_count(self, obj):
        return obj.periods.count()

    @admin.action(description='Generate 12 accounting period')
    def generate_periods(self, request, queryset):
        from .services import generate_periods as gen

        for fy in queryset:
            gen(fy)
        self.message_user(request, f'{queryset.count()} fiscal year diproses.')
        return queryset

    @admin.action(description='Tutup buku tahunan (jurnal penutup -> Laba Ditahan)')
    def close_fiscal_year_action(self, request, queryset):
        from .services import close_fiscal_year

        done, errors = 0, []
        for fiscal_year in queryset:
            try:
                close_fiscal_year(fiscal_year, user=request.user)
                done += 1
            except Exception as exc:
                errors.append(f'{fiscal_year.company.company_code} {fiscal_year.name}: {exc}')
        if done:
            self.message_user(request, f'{done} fiscal year ditutup (jurnal penutup dibuat).')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)

    @admin.action(description='Buka kembali tutup buku tahunan (reversal jurnal penutup)')
    def reopen_fiscal_year_action(self, request, queryset):
        from .services import reopen_fiscal_year

        done, errors = 0, []
        for fiscal_year in queryset:
            try:
                reopen_fiscal_year(fiscal_year, user=request.user)
                done += 1
            except Exception as exc:
                errors.append(f'{fiscal_year.company.company_code} {fiscal_year.name}: {exc}')
        if done:
            self.message_user(request, f'{done} fiscal year dibuka kembali.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)


@admin.register(AccountingPeriod)
class AccountingPeriodAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = [
        'period_code', 'period_name', 'company', 'start_date', 'end_date',
        'status', 'journal_count',
    ]
    list_filter = ['company', 'status', 'fiscal_year']
    search_fields = ['period_code', 'period_name']
    actions = ['start_closing', 'close_period', 'reopen_period', 'revalue_fx']
    mutating_actions = ['start_closing', 'close_period', 'reopen_period', 'revalue_fx']
    readonly_fields = ['closing_checklist']

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            company__in=request.user.allowed_companies(request))

    def get_urls(self):
        return [
            path('opening-balance/', self.admin_site.admin_view(self.opening_balance_view),
                 name='gl_opening_balance'),
        ] + super().get_urls()

    def opening_balance_view(self, request):
        """Input saldo awal (migrasi) per company: upload CSV -> preview -> posting.

        Bukan ModelAdmin karena jurnal saldo awal menulis JournalEntry lewat
        service yang sudah mengurus validasi (akun milik company, balance,
        period OPEN). Alur preview-commit meniru Import CSV CoA.
        """
        import csv as csv_module

        from django.core.exceptions import ValidationError as DjangoValidationError
        from django.http import HttpResponseRedirect
        from django.template.response import TemplateResponse
        from django.urls import reverse

        from .engine import get_period
        from .errors import AccountingError
        from .management.commands.load_opening_balance import map_header, parse_amount
        from .services import post_opening_balance

        if not self.has_view_permission(request):
            raise PermissionDenied
        can_post = self.has_change_permission(request)
        companies = request.user.allowed_companies()
        session_key = 'gl_opening_balance_rows'

        company = None
        requested = request.POST.get('company') or request.GET.get('company')
        if requested:
            company = companies.filter(pk=requested).first()
            if company is None:
                raise PermissionDenied('Company tidak ada di daftar yang boleh diakses.')
        company = company or companies.first()

        raw_date = request.POST.get('as_of') or request.GET.get('as_of')
        as_of = parse_date(raw_date) if raw_date else None
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': gettext_lazy('Input Opening Balance'),
            'companies': companies,
            'company': company,
            'as_of': raw_date,
            'can_post': can_post,
            'preview': None,
            'errors': [],
            'totals': None,
        }
        if company is None:
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)

        if request.method == 'GET' and request.GET.get('template'):
            # Template berisi akun postable company terpilih (kode sudah ada di COA),
            # tinggal isi debit/kredit. BOM + CRLF supaya Excel tidak rusak.
            accounts = Account.objects.filter(
                company=company, is_postable=True, is_active=True).order_by('account_code')
            rows = [['kode_akun', 'nama_akun', 'debit', 'kredit']] + [
                [a.account_code, a.account_name, '', ''] for a in accounts]
            response = HttpResponse(csv_io.render_csv(rows),
                                    content_type='text/csv; charset=utf-8')
            response['Content-Disposition'] = (
                f'attachment; filename="saldo-awal-{company.company_code}.csv"')
            return response

        if request.method == 'POST' and request.POST.get('confirm'):
            if not can_post:
                raise PermissionDenied('Hak ubah period diperlukan untuk posting saldo awal.')
            rows = request.session.pop(session_key, None) or []
            if not rows or as_of is None:
                self.message_user(request, 'Preview kadaluarsa, upload ulang CSV.',
                                  level=messages.WARNING)
                return TemplateResponse(request, 'admin/gl/opening_balance.html', context)
            accounts = {a.pk: a for a in Account.objects.filter(
                company=company, pk__in=[row[0] for row in rows])}
            payload = [(accounts[pk], Decimal(debit), Decimal(credit))
                       for pk, debit, credit in rows if pk in accounts]
            try:
                journal = post_opening_balance(company, as_of, payload, user=request.user)
            except (DjangoValidationError, AccountingError) as exc:
                context['errors'] = [str(exc)]
                return TemplateResponse(request, 'admin/gl/opening_balance.html', context)
            self.message_user(
                request, f'{company.company_code}: jurnal saldo awal {journal.journal_number} dibuat.')
            return HttpResponseRedirect(reverse('admin:gl_accountingperiod_changelist'))

        if request.method != 'POST':
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)

        uploaded = request.FILES.get('csv_file')
        if uploaded is None:
            context['errors'] = ['Pilih file CSV dulu.']
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)
        if as_of is None:
            context['errors'] = ['Isi tanggal saldo awal (YYYY-MM-DD).']
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)

        # Validasi awal: period harus ada & belum ditutup.
        try:
            get_period(company, as_of)
        except AccountingError as exc:
            context['errors'] = [str(exc)]
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)

        rows, errors, preview = [], [], []
        total_debit = total_credit = Decimal('0')
        text = uploaded.read().decode('utf-8-sig')
        reader = csv_module.reader(text.splitlines())
        header = next(reader, None)
        if header is None:
            context['errors'] = ['File CSV kosong.']
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)
        try:
            mapping = map_header(header)
        except Exception as exc:
            context['errors'] = [str(exc)]
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)

        def cell(cells, key):
            index = mapping.get(key)
            return cells[index] if index is not None and index < len(cells) else ''

        for number, cells in enumerate(reader, start=2):
            if not any((c or '').strip() for c in cells):
                continue
            code = cell(cells, 'account_code').strip()
            account = Account.objects.filter(company=company, account_code=code).first()
            if account is None:
                errors.append(f'Baris {number}: akun {code!r} tidak ada di {company.company_code}.')
                continue
            try:
                debit = parse_amount(cell(cells, 'debit'))
                credit = parse_amount(cell(cells, 'credit'))
            except Exception as exc:
                errors.append(f'Baris {number}: {exc}')
                continue
            rows.append([account.pk, str(debit), str(credit)])
            preview.append({'account': account, 'debit': debit, 'credit': credit})
            total_debit += debit
            total_credit += credit

        def _fmt_currency_idr(v: Decimal) -> str:
            # Format IDR sederhana: tanpa simbol, grup ribuan titik, desimal koma 2 angka jika ada
            if v is None:
                return '-'
            # Normalisasi
            q = v.quantize(Decimal('0.01'))
            sign = '-' if q < 0 else ''
            qabs = abs(q)
            # Pisah integer/desimal
            i, d2 = format(qabs, 'f').rstrip('0').rstrip('.'), ''
            # format dengan 2 desimal jika pecahan
            s2 = format(qabs, 'f')
            if '.' in s2:
                idp, dp = s2.split('.')
                dp2 = dp[:2].ljust(2, '0')
                i2 = idp.lstrip('0') or '0'
            else:
                i2, dp2 = s2.lstrip('0') or '0', '00'
            # grup ribuan titik
            grouped = ''
            rev = i2[::-1]
            for j, ch in enumerate(rev, start=1):
                grouped += ch
                if j % 3 == 0 and j != len(rev):
                    grouped += '.'
            grouped_i = grouped[::-1] or '0'
            return f'{sign}{grouped_i},{dp2}'

        context['preview'] = preview
        context['errors'] = errors
        context['totals'] = {'debit': total_debit, 'credit': total_credit,
                             'balanced': total_debit == total_credit and total_debit > 0,
                             'debit_fmt': _fmt_currency_idr(total_debit),
                             'credit_fmt': _fmt_currency_idr(total_credit)}
        # Format preview amounts
        for pr in preview:
            pr['debit_fmt'] = _fmt_currency_idr(pr['debit'])
            pr['credit_fmt'] = _fmt_currency_idr(pr['credit'])
        if not preview:
            context['errors'] = errors or ['Tidak ada baris valid di CSV.']
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)
        if errors:
            # Ada baris bermasalah: tahan commit, minta perbaiki file.
            return TemplateResponse(request, 'admin/gl/opening_balance.html', context)
        request.session[session_key] = rows
        return TemplateResponse(request, 'admin/gl/opening_balance.html', context)

    @admin.display(description='Journals')
    def journal_count(self, obj):
        return obj.journal_entries.count()

    @admin.display(description='Checklist closing')
    def closing_checklist(self, obj):
        from django.utils.html import format_html

        from .services import period_closing_checklist

        if obj is None or obj.pk is None:
            return '-'
        rows = []
        for item in period_closing_checklist(obj):
            mark = 'OK' if item['done'] else ('WAJIB' if item['blocking'] else 'cek')
            rows.append(
                format_html('<li>{} <strong>{}</strong> &mdash; {}</li>',
                            f'[{mark}]', item['label'], item['detail'])
            )
        return format_html('<ul style="margin:0;padding-left:18px">{}</ul>', format_html(''.join(rows)))

    @admin.action(description='Mulai closing (OPEN -> CLOSING, jalankan checklist)')
    def start_closing(self, request, queryset):
        from .services import start_closing as do_start

        done, errors = 0, []
        for period in queryset:
            try:
                do_start(period, user=request.user)
                done += 1
            except Exception as exc:
                errors.append(f'{period.period_code}: {exc}')
        if done:
            self.message_user(request, f'{done} period masuk tahap CLOSING.')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)

    @admin.action(description='Tutup period (CLOSING -> CLOSED)')
    def close_period(self, request, queryset):
        closed = failed = 0
        for period in queryset:
            if period.status == 'CLOSED':
                continue
            try:
                close_period(period, request.user)
                closed += 1
            except Exception as exc:
                failed += 1
                self.message_user(request, f'{period.period_code}: {exc}', level=messages.ERROR)
        if closed:
            self.message_user(request, f'{closed} period ditutup.')

    @admin.action(description='FX revaluation per period (unrealized gain/loss)')
    def revalue_fx(self, request, queryset):
        from .services import revalue_fx as do_revalue

        for period in queryset:
            try:
                results = do_revalue(period.company, period.end_date)
            except Exception as exc:
                self.message_user(request, f'{period.period_code}: {exc}', level=messages.ERROR)
                continue
            if not results:
                self.message_user(request, f'{period.period_code}: tidak ada posisi valas '
                                            'atau unrealized < 0.01.')
                continue
            for r in results:
                if 'error' in r:
                    self.message_user(request, f'{period.period_code} {r["currency"]}: {r["error"]}',
                                      level=messages.ERROR)
                else:
                    self.message_user(
                        request,
                        f'{period.period_code} {r["currency"]}: unrealized {r["unrealized"]} '
                        f'-> {r["journal"].journal_number}',
                    )

    @admin.action(description='Buka kembali period')
    def reopen_period(self, request, queryset):
        count = 0
        for period in queryset:
            if period.status == 'CLOSED':
                reopen_period(period, request.user, notes='Reopened by admin')
                count += 1
        self.message_user(request, f'{count} period dibuka kembali.')


@admin.register(JournalEntry)
class JournalEntryAdmin(ChangeOnlyActionsMixin, CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = [
        'journal_number', 'journal_date', 'company', 'source', 'status', 'is_reversed',
        'total_debit', 'total_credit', 'balance_check', 'description',
    ]
    list_filter = ['company', 'status', 'source', 'journal_date']
    search_fields = ['journal_number', 'description', 'reference']
    date_hierarchy = 'journal_date'
    inlines = [JournalLineInline]
    readonly_fields = [
        'journal_number', 'period', 'status', 'submitted_by', 'submitted_at',
        'approved_by', 'approved_at', 'posted_by', 'posted_at',
        'voided_by', 'voided_at', 'void_reason', 'reversed_from', 'is_reversed',
        'total_debit', 'total_credit',
    ]
    actions = ['submit_journal', 'approve_journal', 'post_journal', 'reverse_journal', 'void_journal']
    mutating_actions = ['submit_journal', 'approve_journal', 'post_journal', 'reverse_journal', 'void_journal']
    list_select_related = ['company', 'period']

    def get_queryset(self, request):
        qs = super().get_queryset(request).prefetch_related('lines')
        qs = qs.filter(company__in=request.user.allowed_companies(request))
        return qs

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        base = list(self.readonly_fields)
        if obj and obj.status in (JournalStatus.POSTED, JournalStatus.RECONCILED, JournalStatus.VOID):
            return base
        return [f for f in base if f not in ('status',)]

    @admin.display(description='Balance')
    def balance_check(self, obj):
        ok = obj.total_debit == obj.total_credit
        return 'OK' if ok else f'TIDAK BALANCE ({obj.total_debit} / {obj.total_credit})'

    def _run(self, request, queryset, fn, ok_msg):
        done, errors = 0, []
        for obj in queryset:
            try:
                fn(request, obj)
                done += 1
            except (AccountingError, ValueError) as exc:
                errors.append(f'{obj.journal_number}: {exc}')
        if done:
            self.message_user(request, f'{done} journal diproses. {ok_msg}')
        for err in errors:
            self.message_user(request, err, level=messages.ERROR)
        return done

    @admin.action(description='Submit (DRAFT -> SUBMITTED)')
    def submit_journal(self, request, queryset):
        return self._run(request, queryset, lambda r, o: o.submit(), 'Disubmit.')

    @admin.action(description='Approve (SUBMITTED -> APPROVED)')
    def approve_journal(self, request, queryset):
        return self._run(request, queryset, lambda r, o: o.approve(), 'Disetujui.')

    @admin.action(description='Post ke GL (DRAFT/APPROVED -> POSTED)')
    def post_journal(self, request, queryset):
        return self._run(request, queryset, lambda r, o: o.post(r.user), 'Diposting.')

    @admin.action(description='Reversal (POSTED -> VOID + jurnal pembalik)')
    def reverse_journal(self, request, queryset):
        def do(r, o):
            reverse_journal(o, user=r.user, reason='Reversal via admin')
        return self._run(request, queryset, do, 'Direversal.')

    @admin.action(description='Void (DRAFT/SUBMITTED/APPROVED -> VOID)')
    def void_journal(self, request, queryset):
        return self._run(request, queryset, lambda r, o: o.void(r.user, 'Void via admin'), 'Di-void.')


@admin.register(JournalLine)
class JournalLineAdmin(CompanyScopedFiltersMixin, CompanyScopedFormMixin, admin.ModelAdmin):
    list_display = ['journal', 'line_no', 'account', 'description', 'debit', 'credit']
    list_filter = ['journal__company', 'journal__status', 'account']

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('journal', 'account')
        return qs.filter(journal__company__in=request.user.allowed_companies(request))

    def has_add_permission(self, request):
        return False