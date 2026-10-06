# ERP Accounting & Finance

Django Admin + Tailwind CSS + MariaDB (XAMPP). Multi-company, multi-currency, General Ledger dengan
automatic accounting engine. Implementasi PRD v1.0 §39 (MVP).

## Stack

| Komponen | Versi | Catatan |
|----------|-------|---------|
| Python | 3.13 | venv di `.venv` — modul tidak konflik dengan sistem |
| Django | 4.2 LTS | MariaDB 10.4 butuh Django < 5 |
| Database | MariaDB 10.4 (XAMPP) | via **PyMySQL**, bukan mysqlclient |
| Admin theme | bawaan Django 4.2 | tanpa skin pihak ketiga |
| CSS report | Tailwind 3.4 | binary standalone, tanpa npm install |

PyMySQL dipilih karena header MySQL di XAMPPbuilt untuk x86_64 dan tidak bisa
dikompilasi ke Python arm64. PyMySQL pure Python, konek ke MariaDB tanpa build step.

## Install

```bash
bash setup.sh
source .venv/bin/activate
python manage.py runserver
```

Login: http://127.0.0.1:8000/admin/ — `admin` / `admin123`

Dashboard: http://127.0.0.1:8000/admin/dashboard/

Reports: http://127.0.0.1:8000/admin/reports/trial-balance/ (path lama `/admin/reports/` redirect ke dashboard)

Konfigurasi database ada di `.env` (lihat `.env.example`).

## Admin theme — bawaan Django

Admin pakai skin bawaan Django. Branding diatur di `config/settings.py`
(`ADMIN_SITE_HEADER`, `ADMIN_SITE_TITLE`, `ADMIN_INDEX_TITLE`).

`templates/admin/base_site.html` hanya menambah company switcher + link dashboard di header.
CSS Tailwind (`css/dist/styles.css`) **hanya** dipakai halaman report (`templates/reports/`),
tidak di admin — kalau dipaksakan ke admin, styling ulang `.module` / `.submit-row` /
`table#result_list` merusak layout admin.

## Root URL

`/` redirect ke `/admin/` (lalu ke login kalau belum auth).

## Struktur

```
config/          settings, urls, PyMySQL shim
core/            Group, Company, Branch, Department, CostCenter, User+Role,
                 Project + ProjectCost/Milestone/Billing, Product, TaxCode,
                 Currency, ExchangeRate, ApprovalLimit, AuditLog,
                 COA template, Budget + budget-vs-actual, context processor
gl/              Account, FiscalYear, AccountingPeriod, JournalEntry/Line,
                 JournalTemplate, AccountMapping + engine (PRD 12) + period services
ar/              Customer, CustomerInvoice(+Line), CustomerPayment(+Allocation)
ap/              Supplier, SupplierBill(+Line), SupplierPayment(+Allocation), 3-way match
bank/            BankAccount, BankTransaction, BankStatementLine, BankReconciliation
fa/              AssetCategory, FixedAsset, AssetDepreciation, AssetTransaction
inventory/       Warehouse (gudang per branch), Inventory, InventoryTransaction, InventoryCost (cost layer),
                 StockOpname + StockOpnameLine (sensus fisik, variance -> adjustment)
workflow/        ApprovalWorkflow, ApprovalStep, ApprovalRequest
consolidation/   IntercompanyTransaction, ConsolidationRun, ConsolidationAdjustment,
                 translation.py (currency translation + NCI)
notify/          Notification + scanner (9 kategori PRD 33)
reports/         Trial Balance, P&L, Balance Sheet, Cash Flow, GL, AR/AP Aging,
                 Company Comparison, Consolidated, Budget vs Actual, Project Profitability,
                 Cost Accounting, Product Profitability, Customer/Vendor Statement
                 + dashboard
reports/cost.py  PRD 19 - profitability per dimensi (customer/supplier/branch/
                 department/cost center/project), product profitability, statement
templates/       report pages (Tailwind)
```

## Accounting engine

`gl/engine.py` — `JournalBuilder` accumulate line, lalu post sebagai satu journal:

```python
from gl.engine import JournalBuilder, get_account

builder = JournalBuilder(company, journal_date, source=JournalSource.MANUAL)
builder.debit(bank_account, 1_000_000)
builder.credit(revenue_account, 1_000_000)
journal = builder.save(auto_post=True)
```

Master journal:
```python
journal.submit()    # DRAFT -> SUBMITTED
journal.approve()   # SUBMITTED -> APPROVED
journal.post(user)  # APPROVED -> POSTED
reverse_journal(journal, user, reason='koreksi')  # jurnal pembalik, asal jadi is_reversed
```

## Data integrity rules (PRD §37) — tercover test

| Rule | Enforcement |
|------|-------------|
| 1 Company isolation | `get_queryset()` filter `company__in=request.user.allowed_companies(request)` di setiap admin — ikut mempersempit ke company aktif bila switcher dipakai |
| 2 Posted journal immutable | `post()` reject double-post, `void()` reject POSTED |
| 3 Closed period immutable | `post()` + `JournalBuilder.save()` cek `period.status == CLOSED` |
| 4 Balanced journal | `UnbalancedJournalError` bila D ≠ C, di engine dan admin action |
| 5 Currency exchange rate | field `exchange_rate` + `amount_base` per line |
| 6 Intercompany source+target | `Supplier.is_intercompany` + `intercompany_company` |
| 7 Audit trail | signal `post_save`/`post_delete` → `AuditLog`, field-level diff |

## Audit trail

`core/audit.py` — setiap perubahan field pada model financially-tracked dicatat ke
`AuditLog` dengan `previous_value` / `new_value` / user / timestamp. Model terdaftar
di `core/signals.py: AUDITED`. Hard-delete pada transaksi posted tidak diizinkan —
koreksi lewat reversal atau void.

## Reports

Semua report baca dari **General Ledger** (jurnal status POSTED/RECONCILED), bukan
dari dokumen sumber. Filter company / branch / period / cost center.

## Test

```bash
python manage.py seed_data --demo   # 1 company + transaksi demo + user/role demo
python smoke_test.py                # 104 checks: rules + FX + bank + CSV + project + notify + budget + cost
python smoke_http.py                # 59 checks: semua halaman admin & report render 200
python smoke_report_data.py         # pastikan report benar-benar berisi angka
```

`smoke_test.py` jalan dalam transaksi yang di-rollback — tidak pollute database,
aman dijalankan berulang.

`seed_data --demo` mengisi satu company (`A - PT Nusantara Jaya`, IDR) supaya
semua report berisi angka: 2 invoice (IDR + USD @15000), 1 bill, 2 payment
partial, bunga bank, 1 asset, 1 project + cost, plus tren 12 bulan. Sample
persediaan: 2 produk, 2 gudang, 4 transaksi GR/GI, dan 1 stock opname
(`SO-DEMO-001`, status COUNTED, satu baris selisih -2) siap diposting lewat
action "Posting variance ke Inventory + GL". Sample alur persetujuan: 2
workflow (`SupplierBill` 3 tier 0-10jt/10jt-100jt/>100jt, `CustomerPayment` 2
tier) + 2 request (`BILL-DEMO-001` PENDING 30jt, `BILL-TREND-001` APPROVED 5jt).
Setelah itu
`seed_demo_users` ikut jalan: 11 user demo dengan role accounting/finance
(owner, finance, accountant, AR, AP, treasury, tax, budget, project, auditor,
management) dan permission group sesuai tugasnya. Password default `demo123`
(ubah dengan `--demo-password`). Idempotent — jalan dua kali tidak duplikasi.

### Catatan migrate data demo

Sejak seed diubah ke **satu company**, database lama yang sebelumnya dibuat dengan
3 company (A/B/C) masih menyimpan company B dan C. `seed_data` memakai
`get_or_create` dan **tidak pernah menghapus apa pun**, jadi B/C harus dibersihkan
manual bila memang mau contoh satu company.

Database baru: tidak perlu tindakan apa-apa.

```bash
# 1. Seed ulang (aman dijalankan berulang; tidak menghapus data lama)
python manage.py seed_data --demo

# 2. Cek company yang ada
python manage.py shell -c "from core.models import Company; print(list(Company.objects.values_list('company_code', flat=True)))"
```

Kalau B/C mau dihapus, urutannya **anak → induk** karena banyak relasi `PROTECT`.
Urutan yang sudah teruji (planning HAPUS permanen — pastikan sudah backup):

| Urutan | Model | Alasan |
|--------|-------|--------|
| 1 | Notification, ApprovalRequest (`workflow__company`), ApprovalWorkflow | tidak merefer ke model lain |
| 2 | IntercompanyTransaction (`source_company`, `target_company`) | menghapus baris company lain yang menunjuk B/C |
| 3 | BankReconciliation → BankTransaction, CustomerPayment, CustomerInvoice, SupplierPayment, SupplierBill, BankAccount | payment/transaction `PROTECT` ke BankAccount |
| 4 | JournalEntry | period `PROTECT`, line ikut cascade |
| 5 | FixedAsset, AssetCategory, InventoryTransaction (`inventory__company`), Inventory | refer akun/period |
| 6 | AccountMapping, JournalTemplate, AccountingPeriod, FiscalYear | AccountMapping `PROTECT` ke Account |
| 7 | Project, Budget, Product, TaxCode, ApprovalLimit, ExchangeRate | Project & TaxCode `PROTECT` ke Account |
| 8 | Account | baru boleh setelah semua pemakai akun hilang |
| 9 | AuditLog | FK ke company (`SET_NULL`, ribuan baris) — dihapus eksplisit supaya tidak tertinggal sebagai baris yatim |
| 10 | UserCompanyAccess, CostCenter, Department, Branch, Customer, Supplier | terakhir |
| 11 | `company.delete()` | |

Jalankan dalam satu `transaction.atomic()` supaya kalau ada langkah terlewat,
semuanya rollback — bukan setengah terhapus.

Mulai bersih (paling aman kalau isi database tidak sayang):

```bash
# HATI-HATI: menghapus SELURUH isi database, bukan hanya company B/C
python manage.py flush --no-input
python manage.py migrate
python manage.py seed_data --demo
```

Efek samping setelah jadi satu company:

- Switcher company di header admin hilang otomatis (`available_companies.count > 1`
  baru menampilkan form ganti company).
- Blok intercompany tidak lagi di-seed karena `peers` kosong; `_demo_data`
  melewatinya dengan `if peers:`.
- Jurnal intercompany milik company A yang menunjuk B/C ikut terhapus saat B/C
  dihapus (baris itu memang tidak valid tanpa company tujuan).

Password user demo pada seed ulang: `seed_demo_users` **tidak menimpa** password
user yang sudah ada (biar refresh demo tidak mereset kredensial), kecuali kolom
password-nya rusak/bukan hash — itu akan dikembalikan ke `--password`. Untuk
memaksa ganti password semua user demo, set manual:

```bash
python manage.py shell -c "
from core.models import User
for u in User.objects.filter(username__startswith='demo_'):
    u.set_password('demo123'); u.save(update_fields=['password'])
"
```

## Modul accounting turunan

Journal tidak ditulis manual. Semua modul memanggil `gl.engine.JournalBuilder`:

| Modul | Pemicu | Jurnal |
|-------|--------|--------|
| AR | post invoice | Dr AR / Cr Revenue / Cr VAT Output |
| AR | post payment | Dr Bank / Cr AR (+ fee ke bank charge) |
| AP | post bill | Dr Expense/Inventory, Dr Input VAT / Cr AP |
| AP | post payment | Dr AP / Cr Bank |
| FA | depreciate | Dr Depreciation Expense / Cr Accumulated Depreciation |
| FA | dispose | Dr Akumulasi + Dr Kas / Cr Aset + gain/loss |
| Inventory | goods issue | Dr COGS / Cr Inventory |
| Intercompany | post | Dr IC Receivable / Cr IC Revenue |
| FX revaluation | AccountingPeriod → "FX revaluation" | Cr FX Gain / Dr FX Adjust (atau sebaliknya) |
| Project | post cost | Dr WIP / Cr AP |
| Project | post billing | Dr AR + Dr Retensi / Cr Revenue |
| Bank | post receipt | Dr Bank / Cr Revenue |
| Bank | post payment | Dr Expense / Cr Bank |
| Bank | post transfer | Dr rekening tujuan / Cr rekening asal |
| Bank | post charge | Dr 6501 Biaya Bank / Cr Bank |
| Bank | post interest | Dr Bank / Cr Revenue |

## Bank & Cash transaction (PRD 15)

`BankTransaction` punya `tx_type` (RECEIPT / PAYMENT / TRANSFER / CHARGE / INTEREST).
Aksi **Post ke GL** di daftar Bank Transaction memanggil `post_bank_transaction()`:

| tx_type | Jurnal |
|---------|--------|
| RECEIPT | Dr rekening / Cr pendapatan |
| PAYMENT | Dr beban / Cr rekening |
| TRANSFER | Dr rekening tujuan / Cr rekening asal (butuh `counterparty_bank_account`, rekening wajib beda) |
| CHARGE | Dr 6501 Biaya Bank / Cr rekening |
| INTEREST | Dr rekening / Cr pendapatan bunga |

Akun pendapatan/beban bisa di-override lewat Account Mapping `BANK/RECEIPT_INCOME`,
`BANK/PAYMENT_EXPENSE`, `BANK/INTEREST_INCOME`. Kalau mapping belum diisi, ambil
akun postable pertama di kategori revenue/expense company tersebut.

## Neraca banding (perbandingan antar tahun)

`/admin/reports/balance-comparison/` - saldo kumulatif tiap akun pada **dua tanggal**
(default: akhir tahun lalu vs hari ini; filter `start` dan `end`). Kolom: saldo
tanggal pembanding, saldo tanggal terkini, dan perubahan. Akun yang hanya muncul di
satu sisi tetap tampil (sisi lain 0), jadi akun baru/tertutup kelihatan. Total baris
harus seimbang karena saldo diambil dari GL posted.

## Saldo awal (migrasi dari sistem lama)

Report menghitung saldo neraca kumulatif dari GL, jadi company baru cukup diberi
**satu jurnal saldo awal** - tidak perlu mengimpor seluruh riwayat transaksi.

Lewat UI: **/admin/gl/accountingperiod/** &rarr; tombol **"Input Saldo Awal"**
(pilih company + tanggal, **Unduh template CSV**, isi debit/kredit, upload, preview, lalu posting). Atau lewat CLI:

```bash
python manage.py load_opening_balance --company A --date 2025-01-01 \
    --file saldo_awal.csv [--reference OB/2025] [--dry-run]
```

Format CSV (header boleh Indonesia/Inggris, urutan bebas, angka boleh
`1.234.567,89` atau `1234567.89`):

```csv
kode_akun,nama_akun,debit,kredit
1101,Kas,"50.000.000",
1201,Piutang Usaha,"25.000.000",
2101,Utang Usaha,,"15.000.000",
3101,Modal,,"60.000.000"
```

Kolom `nama_akun` opsional (hanya keterangan, diabaikan saat parsing). Tombol
**Unduh template CSV** di halaman input sudah mengisi kode + nama akun company
terpilih, tinggal isi debit/kredit.

Jurnal dibuat dengan sumber `OPENING_BALANCE` dan reference default
`OB/<tanggal>`; menjalankan ulang dengan tanggal sama ditolak (idempotent).
Total debit harus sama dengan total credit, akun wajib milik company dan
postable, dan tanggal harus jatuh di period yang masih OPEN. `--dry-run`
memvalidasi + menampilkan total tanpa menulis.

## Tutup buku (month-end & year-end)

Alur bulanan, di `/admin/gl/accountingperiod/`:

```text
OPEN -> (action "Mulai closing") -> CLOSING -> (action "Tutup period") -> CLOSED
```

- Aksi **Mulai closing** menjalankan checklist month-end otomatis. Yang menghalangi
  (flag `blocking`): jurnal belum posted, jurnal posted tidak balance, invoice AR draft,
  tagihan AP draft, transaksi bank masih DRAFT. Item manual (penyusutan aset, FX
  revaluation, reconciliation) tampil sebagai info. Checklist juga muncul di form
  detail period sebagai readonly field "Checklist closing".
- **Tutup period** mengunci period; posting ke period CLOSED ditolak
  (`JournalBuilder.save` -> `PeriodLockedError`).
- **Buka kembali period** ditolak kalau fiscal year-nya sudah ditutup.

Alur tahunan, di `/admin/gl/fiscalyear/` (action **Tutup buku tahunan**):

1. Semua period fiscal year harus sudah CLOSING atau CLOSED (tutup buku bulanan selesai).
2. Service `close_fiscal_year()` membuka period terakhir sementara, lalu membuat **satu
   jurnal penutup** bertanggal `end_date` (source `CLOSING`, reference `YE/<tahun>`):
   tiap akun P&L (kategori 4/5/6) dinolkan, selisihnya ke akun Laba Ditahan
   (`RETAINED_EARNINGS` / kode 3201). Laba -> credit, rugi -> debit.
3. Period dikunci ulang dan `FiscalYear.is_closed = True`. Idempotent: panggil ulang
   tidak membuat jurnal kedua.

**Opening balance** tidak perlu jurnal khusus: saldo akun neraca terbawa karena report
menghitung kumulatif dari GL (`reports/engine.py` `trial_balance` mengambil semua baris
sebelum `start` sebagai opening). Jurnal penutup hanya membuat akun P&L nol di tahun
berikutnya, yang memang perilaku tutup buku tahunan.

## FX revaluation (PRD 23)

Aksi **FX revaluation** di daftar Accounting Period. Hanya akun moneter **neraca**
(asset/liability) dalam valuta asing yang dihitung — akun P&L tidak ikut, kalau tidak
posisi valuta terhitung dua kali. Jurnal `FXREV/` lama otomatis dikecualikan supaya
revaluasi berikutnya tidak akumulatif.

Kurs yang dipakai = ExchangeRate milik **company itu sendiri** (terbaru <= tanggal);
hanya kalau tidak ada, baru pakai rate global (`company__isnull`). Tanpa filter company,
kurs bisa bocor antar company — dulu `order_by('-rate_date')` dengan rate_date identik
mengambil baris acak dari company lain, jadi Company A bisa direvaluasi dengan kurs
Company B. Regression test ada di `smoke_test.py` ("FX revaluation pakai kurs company
sendiri").

```
posisi valuta 1000 USD dicatat @ 15000 -> base 15.000.000
kurs jadi 16000 -> unrealized +1.000.000
   Cr 4320 Keuntungan Selisih Kurs / Dr 1390 Penyesuaian
kurs jadi 14000 -> unrealized -1.000.000
   Dr 6920 Kerugian Selisih Kurs / Cr 1390 Penyesuaian
kurs tetap -> tidak ada jurnal
```

## Project accounting (PRD 18)

`ProjectCost` → Dr WIP / Cr AP. `ProjectBilling` → Dr AR + Dr Retensi / Cr Revenue
(gross diakui penuh, retensi ditahan di akun `1203` Piutang Retensi). Milestone
.track `billed_amount` dan `is_billed` otomatis saat billing diposting.

Report `/admin/reports/project-profitability/` baca dari journal posted.
Cost = Dr akun beban (kategori 6) **+ Dr akun WIP** — tanpa WIP, cost selalu nol
karena biaya proyek masuk asset dulu sebelum direkognisi ke P&L.

## Consolidation: Currency Translation & NCI (PRD 23, 25)

`consolidation/translation.py` - reporting currency group = base currency company
dengan ownership terbesar (default IDR). Kurs diambil dari ExchangeRate
terakhir <= tanggal, company-specific diutamakan sebelum global.

NCI = minority% (100 - ownership%) x laba anak perusahaan.
`net_profit_attributable = net_profit - nci`.

- `/admin/reports/consolidated/` - Consolidated P&L + NCI detail + IC exceptions
- `/admin/reports/company-comparison/` - tiap company dikonversi ke reporting
  currency dulu, baru dijumlahkan (sebelumnya ada bug menjumlahkan IDR + USD)

Contoh: B (USD) revenue 10,000 @ 15000 = 150jt IDR; C (IDR, 80%) 200jt;
total 350jt, NCI 40jt (20% x 200jt), attributable 310jt.

## Cost Accounting & Management Reports (PRD 19, 27)

`reports/cost.py` menghitung profitability dari GL (journal POSTED) per dimensi:
customer, supplier, branch, department, cost center, project.

- `/admin/reports/cost-accounting/?dimension=customer` — Revenue / COGS /
  Gross Profit / Expense / Profit / Margin per dimensi
- `/admin/reports/product-profitability/` — revenue per produk dari invoice
  line, COGS dari akun `cogs_account` produk (gross margin, tidak dialokasi
  per transaksi karena JournalLine tak punya field product)
- `/admin/reports/customer-statement/?customer=<id>` — invoice + payment,
  running balance (PRD 13.2)
- `/admin/reports/vendor-statement/?supplier=<id>` — bill + payment,
  running balance (PRD 14)

Line tanpa dimensi diabaikan — tak bisa diatribusikan ke customer/project tertentu.

## Budget vs Actual (PRD 22)

Report `/admin/reports/budget-vs-actual/` menampilkan Budget / Actual / Variance /
Variance % per company + akun + dimensi. Actual dari GL (journal POSTED).

Budget annual dibagi rata ke 12 bulan, budget monthly dijumlahkan per periode —
keduanya bisa digabung. Variance punya arah: revenue over budget = favourable,
cost over budget = unfavourable. Akun yang ada actual tapi belum ada budget-nya
ikut ditampilkan (budget nol).

Budget model: annual/monthly, dengan dimensi opsional company/branch/department/
cost_center/project. Status DRAFT -> SUBMITTED -> APPROVED/REJECTED.

## Notification (PRD 33)

Scanner dijalankan manual atau lewat cron:

```bash
python manage.py scan_notifications                    # semua company
python manage.py scan_notifications --company A         # satu company
python manage.py scan_notifications --only invoice_overdue failed_journal
```

9 kategori: `invoice_overdue`, `payment_due`, `approval_pending`,
`bank_recon_exception`, `intercompany_mismatch`, `period_closing`,
`failed_journal`, `budget_exceeded`, `failed_integration`.

Dedup lewat `fingerprint` unik (`kategori:model:id`) — scan kedua tidak
mengulang notifikasi yang sudah ada, jadi aman dipanggil harian.

## Import bank statement (PRD 16)

Form **Change** pada Bank Reconciliation punya field upload CSV. Format header
fleksibel (`tanggal/date`, `referensi/reference`, `uraian/description`,
`debit/debet`, `credit/kredit`, `saldo/balance`), header boleh dihilangkan,
tanggal `YYYY-MM-DD` atau `DD/MM/YYYY`, angka boleh berformat ribuan atau kurung
negatif `(150.000)`. Baris tidak valid dilaporkan sebagai pesan, bukan digagalkan.

## Company switcher

Dropdown di header admin (`/admin/core/company/switch/?company_id=<id>`) menyimpan company
aktif di session `active_company_id`. Company switcher hanya menampilkan company yang
diizinkan user — switcher bukan loophole Rule 1.

Efeknya nyata: setiap `get_queryset()` admin dan setiap report difilter ke company aktif,
jadi `allowed_companies(request)` mengembalikan **satu** company kalau ada session, atau
semua company yang diizinkan kalau tidak ada. `User.allowed_companies()` tanpa argumen
tetap mengembalikan semua (dipakai switcher untuk validasi target).

Superuser juga ikut di-scope — superuser bukan berarti bypass switcher; dia
tetap dibatasi ke company yang dipilih. Kalau butuh lihat semua, pilih "-- semua --".

## Yang belum ada

Email delivery (scanner sudah menghasilkan notifikasi, belum dikirim via SMTP),
bank API integration (MT940), percentage-of-completion revenue recognition.
`three_way_match()` di
`ap/services.py` baru validasi referensi PO/receipt — pencocokan quantity/harga
butuh model `PurchaseOrder` / `GoodsReceipt`.