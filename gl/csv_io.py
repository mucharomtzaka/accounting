"""Import/export Chart of Accounts lewat CSV.

Pakai stdlib `csv` saja - project ini tidak punya django-import-export /
openpyxl, dan menambah dependency untuk satu format file tidak sepadan.

Kontrak kolom (header boleh pakai nama Indonesia atau Inggris, urutan bebas):

    kode_akun, nama_akun, kategori, tipe_akun, saldo_normal, kode_induk,
    kode_mata_uang, bisa_posting, akun_kontrol, akun_rekonsiliasi, aktif, deskripsi

Minimal wajib ada `kode_akun`, `nama_akun`, dan `kategori`. Kolom yang tidak
ada di file TIDAK disentuh saat update - jadi import bisa incremental.
`is_system` tidak pernah ditulis dari CSV supaya akun engine tidak bisa
diubah lewat import.

Fase dipisah agar admin bisa preview sebelum commit:

    parse_account_csv(file)            -> (header_map, rows, errors)   # tanpa DB
    validate_rows(header_map, rows)    -> (clean, errors)              # tanpa DB
    build_plan(clean, company)         -> plan                         # baca DB, read-only
    commit_plan(plan, company, user)   -> dict                         # atomic write
"""

import csv
import io

# Kanal -> alias header yang diterima (sudah lowercase, spasi jadi underscore).
COLUMN_ALIASES = {
    'account_code': {'account_code', 'accountcode', 'code', 'kode_akun', 'kode', 'kode_coa'},
    'account_name': {'account_name', 'accountname', 'name', 'nama_akun', 'nama', 'nama_coa'},
    'account_category': {'account_category', 'category', 'accountcategory',
                         'kategori', 'kategori_akun', 'kategori_coa'},
    'account_type': {'account_type', 'type', 'accounttype', 'tipe_akun', 'tipe', 'jenis_akun'},
    'normal_balance': {'normal_balance', 'normalbalance', 'saldo_normal', 'saldo', 'normal'},
    'parent_code': {'parent_code', 'parentcode', 'parent', 'kode_induk', 'induk', 'kode_parent'},
    'currency_code': {'currency_code', 'currencycode', 'currency', 'kode_mata_uang', 'mata_uang'},
    'is_postable': {'is_postable', 'postable', 'bisa_posting', 'posting'},
    'is_control_account': {'is_control_account', 'control_account', 'akun_kontrol', 'kontrol'},
    'is_reconciliation_account': {'is_reconciliation_account', 'reconciliation_account',
                                  'akun_rekonsiliasi', 'rekonsiliasi'},
    'is_active': {'is_active', 'active', 'aktif'},
    'description': {'description', 'deskripsi', 'keterangan', 'catatan'},
}

COLUMNS = list(COLUMN_ALIASES)

REQUIRED_COLUMNS = ['account_code', 'account_name', 'account_category']
BOOL_COLUMNS = ['is_postable', 'is_control_account', 'is_reconciliation_account', 'is_active']

ALIAS_LOOKUP = {}
for _canonical, _aliases in COLUMN_ALIASES.items():
    for _alias in _aliases:
        ALIAS_LOOKUP[_alias] = _canonical

# Boolean ditulis Y/T dan N/F supaya aman di Excel Indonesia: 'T' berarti
# "tidak", bukan "true". Karena itu 't'/'f' tidak pernah dibaca sebagai true.
TRUE_VALUES = {'y', 'yes', 'true', '1', 'ya', 'benar', 'on', 'iya'}
FALSE_VALUES = {'n', 'no', 'false', '0', 't', 'f', 'tidak', 'off', 'bukan'}

CATEGORY_ALIASES = {
    1: ['1', 'asset', 'aset', 'aktiva', 'harta'],
    2: ['2', 'liability', 'liabilities', 'kewajiban', 'utang', 'liabilitas'],
    3: ['3', 'equity', 'ekuitas', 'modal', 'equitas'],
    4: ['4', 'revenue', 'revenues', 'pendapatan', 'sales', 'penjualan'],
    5: ['5', 'cogs', 'cost of goods sold', 'cost of sales', 'hpp', 'harga pokok penjualan'],
    6: ['6', 'expense', 'expenses', 'beban', 'operating expense'],
}


def _build_category_lookup():
    """Alias kategori + label resmi model ('1 - Asset') supaya file yang
    dielectric dari template kita sendiri bisa langsung diimport."""
    from gl.models import AccountCategory

    lookup = {}
    for value, aliases in CATEGORY_ALIASES.items():
        label = AccountCategory(value).label
        for alias in list(aliases) + [label.lower(), label.lower().split(' - ')[-1]]:
            lookup[alias] = value
    return lookup


CATEGORY_LOOKUP = _build_category_lookup()

DEBIT_WORDS = {'d', 'dr', 'dk', 'debit', 'debet'}
CREDIT_WORDS = {'c', 'cr', 'kr', 'credit', 'kredit'}

# Saldo normal default mengikuti kategori, dipakai saat kolom kosong.
CATEGORY_DEFAULT_BALANCE = {1: 'D', 2: 'C', 3: 'C', 4: 'C', 5: 'D', 6: 'D'}

MAX_ROWS = 5000

# Label field model untuk tampilan ringkasan perubahan di halaman preview.
FIELD_LABELS = {
    'account_name': 'nama',
    'account_category': 'kategori',
    'account_type': 'tipe',
    'normal_balance': 'saldo normal',
    'currency': 'mata uang',
    'parent': 'kode induk',
    'is_postable': 'bisa posting',
    'is_control_account': 'akun kontrol',
    'is_reconciliation_account': 'akun rekonsiliasi',
    'is_active': 'aktif',
    'description': 'deskripsi',
}

TEMPLATE_ROWS = [
    ['1000', 'ASET', '1 - Asset', 'ASSET', 'D', '', '', 'T', 'T', 'T', 'Y', 'Kelompok aset'],
    ['1100', 'Kas & Bank', '1 - Asset', 'CASH_BANK', 'D', '1000', '', 'T', 'T', 'T', 'Y', ''],
    ['1101', 'Kas Tunai', '1 - Asset', 'CASH', 'D', '1100', 'IDR', 'Y', 'T', 'T', 'Y', ''],
    ['1201', 'Piutang Usaha', '1 - Asset', 'ACCOUNT_RECEIVABLE', 'D', '1200', 'IDR', 'Y', 'T', 'Y', 'Y', ''],
]


def _norm(value):
    return str(value or '').strip()


def _blank_to_none(value):
    return _norm(value) or None


def template_rows():
    """Baris CSV template: header + contoh. Cell kosong berarti kolom diabaikan."""
    return [COLUMNS] + [list(row) for row in TEMPLATE_ROWS]


def render_csv(rows):
    """Serialisasi baris jadi teks CSV (CRLF + BOM supaya Excel tidak rusak)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\r\n')
    for row in rows:
        writer.writerow(row)
    return '\ufeff' + buffer.getvalue()


def export_rows(queryset):
    """Konversi queryset Account (sudah select_related) jadi baris CSV."""
    rows = [COLUMNS]
    for acc in queryset:
        rows.append([
            acc.account_code,
            acc.account_name,
            acc.account_category,
            acc.account_type,
            acc.normal_balance,
            acc.parent.account_code if acc.parent_id else '',
            acc.currency.code if acc.currency_id else '',
            'Y' if acc.is_postable else 'T',
            'Y' if acc.is_control_account else 'T',
            'Y' if acc.is_reconciliation_account else 'T',
            'Y' if acc.is_active else 'T',
            acc.description,
        ])
    return rows


def _map_header(cells):
    """Petakan baris header -> {canonical: index}. None kalau bukan header."""
    mapped, duplicates = {}, []
    for index, cell in enumerate(cells):
        key = _norm(cell).lower().replace(' ', '_').replace('-', '_')
        if not key:
            continue
        canonical = ALIAS_LOOKUP.get(key)
        if canonical is None:
            continue
        if canonical in mapped:
            duplicates.append(canonical)
        mapped[canonical] = index
    # Minimal 2 kolom dikenali supaya baris data yang kebetulan mirip header
    # tidak salah dibaca sebagai header.
    return {'mapped': mapped, 'duplicates': duplicates} if len(mapped) >= 2 else None


def _read_all(file_obj):
    """Baca file CSV dan tebak delimiter-nya.

    Excel Indonesia sering menyimpan CSV dengan `;` karena `,` dipakai sebagai
    desimal, jadi delimiter perlu ditebak bukan hardcode koma.
    """
    try:
        raw = list(csv.reader(file_obj, delimiter=_guess_delimiter(file_obj)))
    except csv.Error as exc:
        return None, f'File CSV tidak bisa dibaca ({exc}).'
    return raw, None


def _guess_delimiter(file_obj):
    sample = file_obj.read(8192)
    if hasattr(file_obj, 'seek'):
        file_obj.seek(0)
    try:
        return csv.Sniffer().sniff(sample, delimiters=',;\t|').delimiter
    except csv.Error:
        return ','


def parse_account_csv(file_obj):
    """Baca CSV jadi baris mentah. Tanpa akses DB.

    Kembalikan `(header_map, rows, errors)`. `header_map` None kalau file tidak
    punya header; baris lalu dibaca memakai urutan `COLUMNS`.
    """
    if hasattr(file_obj, 'read') and not hasattr(file_obj, 'encoding'):
        from io import TextIOWrapper

        file_obj = TextIOWrapper(file_obj, encoding='utf-8-sig')

    try:
        raw, error = _read_all(file_obj)
    except UnicodeDecodeError as exc:
        return None, [], [{'line': 0, 'code': '', 'reason': f'File bukan teks UTF-8 valid ({exc}).'}]
    if error:
        return None, [], [{'line': 0, 'code': '', 'reason': error}]

    body = [(i + 1, cells) for i, cells in enumerate(raw) if any(_norm(c) for c in cells)]
    if not body:
        return None, [], [{'line': 0, 'code': '', 'reason': 'File CSV kosong.'}]

    first_line, first_cells = body[0]
    detected = _map_header(first_cells)
    if detected:
        header_map = detected['mapped']
        errors = [{'line': first_line, 'code': '', 'reason': f'Kolom "{c}" muncul lebih dari sekali.'}
                  for c in detected['duplicates']]
        body = body[1:]
    else:
        header_map = {name: i for i, name in enumerate(COLUMNS)}
        errors = []

    if not errors:
        for canonical in REQUIRED_COLUMNS:
            if canonical not in header_map:
                label = ' / '.join(sorted(COLUMN_ALIASES[canonical]))
                errors.append({'line': first_line if detected else 0, 'code': '',
                               'reason': f'Kolom wajib untuk {canonical} tidak ada di header '
                                         f'(pilihan nama: {label}).'})

    if errors:
        return header_map, [], errors

    if len(body) > MAX_ROWS:
        return header_map, [], [{'line': 0, 'code': '',
                                 'reason': f'Maksimal {MAX_ROWS} baris per import '
                                           f'(file berisi {len(body)}).'}]

    return header_map, [{'line': line, 'raw': cells} for line, cells in body], []


def category_type_name(category):
    """Default `account_type` dari label kategori ('Asset' -> 'ASSET')."""
    from gl.models import AccountCategory

    if not category:
        return 'OTHER'
    return AccountCategory(category).label.split(' - ')[-1].strip().upper()


def validate_rows(header_map, rows):
    """Normalisasi + validasi per baris. Tanpa akses DB.

    Kembalikan `(clean, errors)`. Kolom kosong tidak masuk `clean[i]['values']`
    supaya update tidak menimpa field yang tidak ada di file.
    """
    clean, errors = [], []
    seen = {}

    def cell(name, raw):
        index = header_map.get(name)
        return raw[index].strip() if index is not None and index < len(raw) else ''

    for row in rows:
        line, raw = row['line'], row['raw']
        problems = []
        values = {}

        code = cell('account_code', raw)
        if not code:
            problems.append('kode akun kosong')
        elif not code.isdigit():
            problems.append(f'kode akun "{code}" harus hanya angka')
        elif not 2 <= len(code) <= 20:
            problems.append(f'kode akun "{code}" harus 2-20 digit')
        elif code in seen:
            problems.append(f'kode "{code}" duplikat (sudah dipakai di baris {seen[code]})')
        else:
            seen[code] = line

        name = cell('account_name', raw)
        if not name:
            problems.append('nama akun kosong')
        elif len(name) > 255:
            problems.append('nama akun lebih dari 255 karakter')
        else:
            values['account_name'] = name

        category_raw = _norm(cell('account_category', raw)).lower()
        category = CATEGORY_LOOKUP.get(category_raw)
        if not category_raw:
            problems.append('kategori kosong')
        elif category is None:
            problems.append(f'kategori "{cell("account_category", raw)}" tidak dikenal '
                            '(pakai 1-6 atau aset/liability/equity/revenue/cogs/expense)')
        else:
            values['account_category'] = category

        balance_raw = _norm(cell('normal_balance', raw)).lower()
        if balance_raw:
            if balance_raw in DEBIT_WORDS:
                values['normal_balance'] = 'D'
            elif balance_raw in CREDIT_WORDS:
                values['normal_balance'] = 'C'
            else:
                problems.append(f'saldo normal "{cell("normal_balance", raw)}" harus D/Debit atau C/Credit')
        elif category is not None:
            values['normal_balance'] = CATEGORY_DEFAULT_BALANCE[category]

        account_type = _blank_to_none(cell('account_type', raw))
        if account_type is not None and len(account_type) > 50:
            problems.append(f'tipe akun "{account_type}" lebih dari 50 karakter')
        elif account_type is not None:
            values['account_type'] = account_type

        for flag in BOOL_COLUMNS:
            raw_flag = _norm(cell(flag, raw)).lower()
            if not raw_flag:
                continue
            if raw_flag in TRUE_VALUES:
                values[flag] = True
            elif raw_flag in FALSE_VALUES:
                values[flag] = False
            else:
                problems.append(f'kolom {flag} "{cell(flag, raw)}" harus Y/Ya atau N/Tidak')

        description = _blank_to_none(cell('description', raw))
        if description is not None:
            values['description'] = description

        parent_code = _blank_to_none(cell('parent_code', raw))
        if parent_code and parent_code == code:
            problems.append(f'kode induk "{parent_code}" sama dengan kode akun sendiri')

        currency_code = _blank_to_none(cell('currency_code', raw))
        if currency_code is not None and len(currency_code) > 10:
            problems.append(f'kode mata uang "{currency_code}" tidak dikenal')

        if problems:
            errors.append({'line': line, 'code': code, 'reason': '; '.join(problems)})
            continue

        clean.append({
            'line': line,
            'code': code,
            'name': name,
            'values': values,
            'parent_code': parent_code,
            'currency_code': currency_code,
        })

    return clean, errors


def detect_cycles(parent_of):
    """Kode yang ikut siklus lewat rantai parent. DFS dengan marker 3 warna."""
    GREY, BLACK = 1, 2
    state = {}
    cycles = set()

    def visit(code, trail):
        state[code] = GREY
        trail.append(code)
        parent = parent_of.get(code)
        if parent and parent not in state:
            visit(parent, trail)
        elif parent and state.get(parent) == GREY:
            cycles.update(trail[trail.index(parent):])
        state[code] = BLACK
        trail.pop()

    for code in list(parent_of):
        if code not in state:
            visit(code, [])

    return cycles


def build_plan(rows, company):
    """Bandingkan baris tervalidasi dengan DB. Read-only, tidak menulis apa pun.

    Kembalikan `{'create': [...], 'update': [...], 'unchanged': [...],
    'errors': [...]}`. Tiap item punya `line`, `code`, `name`, `parent_code`,
    dan pada `update`/`unchanged` juga `changes` (`{field: (lama, baru)}`).
    """
    from core.models import Currency
    from gl.models import Account

    existing = {acc.account_code: acc for acc in Account.objects.filter(company=company)}
    file_codes = {row['code'] for row in rows}

    currency_cache = {}
    for code in {r['currency_code'] for r in rows if r['currency_code']}:
        currency_cache[code] = Currency.objects.filter(code=code).first()

    prepared, errors = [], []
    for row in rows:
        values = dict(row['values'])
        if row['currency_code']:
            currency = currency_cache.get(row['currency_code'])
            if currency is None:
                errors.append({'line': row['line'], 'code': row['code'],
                               'reason': f'mata uang "{row["currency_code"]}" tidak dikenal'})
                continue
            values['currency'] = currency

        parent_code = row['parent_code']
        if parent_code and parent_code not in existing and parent_code not in file_codes:
            owner = (Account.objects.filter(account_code=parent_code)
                     .exclude(company=company)
                     .values_list('company__company_code', flat=True).first())
            reason = (f'kode induk "{parent_code}" milik company {owner}, bukan company tujuan'
                      if owner else
                      f'kode induk "{parent_code}" tidak ditemukan (tidak ada di file ini '
                      'maupun di company tujuan)')
            errors.append({'line': row['line'], 'code': row['code'], 'reason': reason})
            continue

        prepared.append({'row': row, 'values': values})

    parent_of = {item['row']['code']: item['row']['parent_code']
                 for item in prepared if item['row']['parent_code']}
    for code in sorted(detect_cycles(parent_of)):
        errors.append({
            'line': next((r['line'] for r in rows if r['code'] == code), 0),
            'code': code,
            'reason': 'kode induk membentuk siklus, mis. 1100 -> 1101 -> 1100',
        })

    if errors:
        return {'create': [], 'update': [], 'unchanged': [], 'errors': errors}

    plan = {'create': [], 'update': [], 'unchanged': [], 'errors': []}
    for item in prepared:
        row = item['row']
        entry = {'line': row['line'], 'code': row['code'], 'name': row['name'],
                 'parent_code': row['parent_code']}

        current = existing.get(row['code'])
        if current is None:
            values = item['values']
            if not values.get('account_type'):
                # Model mewajibkan account_type; untuk akun baru default kan
                # ke nama kategori. Akun lama tidak boleh ditimpa default ini.
                values['account_type'] = category_type_name(values.get('account_category'))
            entry['values'] = values
            plan['create'].append(entry)
            continue

        changes = {field: (getattr(current, field, None), new)
                   for field, new in item['values'].items()
                   if getattr(current, field, None) != new}
        current_parent_code = current.parent.account_code if current.parent_id else None
        if row['parent_code'] != current_parent_code:
            changes['parent'] = (current.parent, existing.get(row['parent_code']))

        entry['changes'] = changes
        # Versi siap tampil untuk template: label field sudah ramah dibaca.
        entry['change_rows'] = [
            {'field': field, 'label': FIELD_LABELS.get(field, field),
             'old': '' if old is None else str(old), 'new': '' if new is None else str(new)}
            for field, (old, new) in changes.items()
        ]
        plan['update' if changes else 'unchanged'].append(entry)

    return plan


def _parents_first(plan):
    """Urutkan entri create supaya induk selalu lebih dulu dibuat dari anaknya.

    Baris CSV boleh menaruh anak sebelum induk; siklus sudah ditolak di
    build_plan, jadi pengurutan ini selalu selesai.
    """
    by_code = {item['code']: item for item in plan['create']}
    ordered, seen = [], set()

    def visit(item):
        if item['code'] in seen:
            return
        seen.add(item['code'])
        if item['parent_code'] in by_code:
            visit(by_code[item['parent_code']])
        ordered.append(item)

    for item in plan['create']:
        visit(item)
    return ordered


def commit_plan(plan, company, user=None):
    """Tulis hasil plan ke DB dalam satu transaksi. Semua atau tidak sama sekali."""
    from django.db import transaction
    from gl.models import Account

    if plan['errors']:
        raise ValueError('Plan masih punya error - tidak ada yang ditulis.')

    created = updated = 0
    with transaction.atomic():
        locked = {acc.account_code: acc
                  for acc in Account.objects.filter(company=company).select_for_update()}

        def resolve(code):
            parent = locked.get(code)
            if parent is None:
                raise ValueError(f'Akun induk "{code}" hilang saat commit.')
            return parent

        for item in _parents_first(plan):
            acc = Account(company=company, account_code=item['code'], **item['values'])
            if item['parent_code']:
                acc.parent = resolve(item['parent_code'])
            if user is not None and getattr(user, 'is_authenticated', False):
                acc.created_by = user
            acc.save()
            locked[acc.account_code] = acc
            created += 1

        for item in plan['update']:
            acc = locked.get(item['code'])
            if acc is None:
                raise ValueError(f'Akun "{item["code"]}" hilang saat commit.')
            for field, (_, new) in item['changes'].items():
                if field == 'parent':
                    new = resolve(new.account_code) if new is not None else None
                setattr(acc, field, new)
            if user is not None and getattr(user, 'is_authenticated', False):
                acc.updated_by = user
            acc.save()
            updated += 1

    return {'created': created, 'updated': updated, 'unchanged': len(plan['unchanged'])}


def plan_summary(plan):
    return (f'{len(plan["create"])} akun baru, {len(plan["update"])} akun diupdate, '
            f'{len(plan["unchanged"])} akun tidak berubah')