# PRD — Accounting & Finance Multi-Company

**Product:** ERP Accounting & Finance
**Version:** 1.0
**Status:** Draft
**Target:** Perusahaan barang, jasa, distribusi, perdagangan, dan project-based business
**Architecture:** Multi-company / Multi-entity / Multi-branch / Multi-currency

---

# 1. Product Overview

Modul **Accounting & Finance** merupakan financial engine dari ERP yang mengintegrasikan transaksi Sales, Purchase, Inventory, Project, Fixed Asset, Tax, Cash & Bank, dan modul operasional lainnya ke dalam General Ledger secara otomatis.

Sistem harus mendukung beberapa perusahaan dalam satu group/holding dengan pemisahan data, accounting configuration, user permission, transaksi, laporan, dan financial period pada level company.

Sistem juga harus mendukung consolidated financial reporting untuk kebutuhan group.

## 1.1 Core Principle

> **One transaction → One source of truth → Automatic accounting**

Setiap transaksi keuangan berasal dari transaksi sumber dan menghasilkan accounting journal secara otomatis.

Contoh:

```text
Sales Order
    ↓
Delivery
    ↓
Sales Invoice
    ↓
Account Receivable
    ↓
Customer Payment
    ↓
Bank
    ↓
Automatic Journal
    ↓
General Ledger
    ↓
Financial Report
```

---

# 2. Goals

## 2.1 Business Goals

1. Menyediakan accounting system untuk banyak company.
2. Mengurangi input transaksi accounting secara manual.
3. Mengintegrasikan accounting dengan Sales, Purchase, Inventory, dan Project.
4. Menyediakan laporan keuangan per company, branch, department, project, dan group.
5. Mendukung intercompany transaction.
6. Mendukung financial consolidation.
7. Meningkatkan kontrol dan auditability transaksi.
8. Mendukung multi-currency.
9. Mempermudah month-end dan year-end closing.
10. Menyediakan financial dashboard untuk management.

## 2.2 Product Goals

Sistem harus mampu:

* Membuat journal otomatis.
* Mengontrol accounting period.
* Mengelola AR/AP.
* Mengelola Cash & Bank.
* Mengelola inventory accounting.
* Mengelola project accounting.
* Mengelola fixed asset.
* Mengelola tax.
* Mengelola budget.
* Mengelola intercompany.
* Menghasilkan consolidated financial report.

---

# 3. Scope

## 3.1 In Scope

* Multi-company
* Multi-branch
* Multi-department
* Multi-currency
* General Ledger
* Chart of Accounts
* Accounts Receivable
* Accounts Payable
* Cash & Bank
* Bank Reconciliation
* Inventory Accounting
* Project Accounting
* Cost Accounting
* Fixed Asset
* Tax
* Budgeting
* Intercompany
* Consolidation
* Financial Closing
* Financial Reporting
* Dashboard
* Approval Workflow
* Audit Trail
* Role & Permission

## 3.2 Out of Scope — MVP

* Payroll engine lengkap
* Manufacturing costing lengkap
* Advanced treasury
* Advanced financial planning
* AI financial forecasting
* Automatic tax filing penuh
* Banking API integration khusus bank tertentu

Fitur tersebut dapat masuk Phase 2/3.

---

# 4. Organization Structure

```text
Group / Holding
│
├── Company A
│   ├── Branch Jakarta
│   ├── Branch Bandung
│   └── Branch Surabaya
│
├── Company B
│   ├── Branch Jakarta
│   └── Branch Semarang
│
└── Company C
    └── Branch Yogyakarta
```

Hierarchy:

```text
Group
  ↓
Company
  ↓
Branch
  ↓
Department
  ↓
Cost Center
  ↓
Project
```

---

# 5. Multi-Company Requirements

## 5.1 Company

Setiap company memiliki accounting environment sendiri.

### Fields

* company_id
* company_code
* legal_name
* display_name
* NPWP
* NIB
* address
* country
* province
* city
* phone
* email
* website
* base_currency
* fiscal_year_start
* tax_registration
* status

## 5.2 Company Isolation

Data berikut harus memiliki company scope:

* Accounts
* Journals
* AR
* AP
* Bank
* Cash
* Tax
* Fixed Asset
* Budget
* Projects
* Inventory Accounting
* Accounting Period

User tidak boleh melihat data company lain kecuali memiliki permission.

---

# 6. Branch Management

Setiap company dapat memiliki banyak branch.

### Features

* Create branch
* Edit branch
* Activate/deactivate branch
* Assign manager
* Assign warehouse
* Assign cost center
* Branch reporting

### Fields

```text
branch_id
company_id
branch_code
branch_name
address
manager_id
default_warehouse_id
cost_center_id
status
```

---

# 7. Department & Cost Center

## 7.1 Department

Contoh:

```text
Sales
Marketing
Operations
Finance
HR
IT
```

## 7.2 Cost Center

Setiap transaksi dapat dikaitkan dengan cost center.

Contoh:

```text
CC-001 Sales
CC-002 Marketing
CC-003 Operations
CC-004 Finance
CC-005 HR
```

---

# 8. User & Permission

## 8.1 Roles

* Super Admin
* Group Finance
* Company Finance
* Accountant
* AR Officer
* AP Officer
* Treasury
* Tax Officer
* Budget Controller
* Project Finance
* Auditor
* Management

## 8.2 Permission

Permission harus mendukung:

* Company
* Branch
* Module
* Menu
* Create
* Read
* Update
* Delete
* Approve
* Post
* Void
* Export

---

# 9. Chart of Accounts

## 9.1 Account Categories

```text
1 ASSET
2 LIABILITY
3 EQUITY
4 REVENUE
5 COGS
6 EXPENSE
```

## 9.2 Features

* Create account
* Edit account
* Account hierarchy
* Account type
* Account code
* Account mapping
* Active/inactive
* Control account
* Reconciliation account
* Company-specific COA
* COA template

## 9.3 Example

```text
1 ASSET
├── 11 Cash & Bank
├── 12 Account Receivable
├── 13 Inventory
└── 14 Fixed Asset

2 LIABILITY
├── 21 Account Payable
├── 22 Tax Payable
└── 23 Accrued Expense

3 EQUITY
├── 31 Paid-in Capital
└── 32 Retained Earnings

4 REVENUE
├── 41 Product Revenue
└── 42 Service Revenue

5 COGS
├── 51 Product COGS
└── 52 Service Cost

6 EXPENSE
├── 61 Operating Expense
├── 62 Salary
├── 63 Marketing
└── 64 Depreciation
```

---

# 10. Accounting Period

## Features

* Create fiscal year
* Create accounting period
* Open period
* Close period
* Reopen period
* Period locking
* Year-end closing

### Status

```text
OPEN
CLOSING
CLOSED
```

Transaksi tidak boleh diposting ke CLOSED period kecuali user memiliki permission khusus.

---

# 11. General Ledger

## Features

* Manual journal
* Automatic journal
* Recurring journal
* Accrual
* Reversal
* Adjustment
* Reclassification
* Journal attachment
* Journal approval
* Journal posting
* Journal void
* Journal audit trail

## Journal Status

```text
DRAFT
  ↓
SUBMITTED
  ↓
APPROVED
  ↓
POSTED
  ↓
RECONCILED
```

---

# 12. Automatic Accounting Engine

Accounting engine harus menggunakan configurable accounting rules.

## 12.1 Sales Invoice

```text
Dr Account Receivable
    Cr Sales Revenue
    Cr VAT Output
```

## 12.2 Purchase Invoice

```text
Dr Inventory / Expense
Dr Input VAT
    Cr Account Payable
```

## 12.3 Customer Payment

```text
Dr Bank
    Cr Account Receivable
```

## 12.4 Supplier Payment

```text
Dr Account Payable
    Cr Bank
```

## 12.5 Inventory Sale

```text
Dr COGS
    Cr Inventory
```

## 12.6 Depreciation

```text
Dr Depreciation Expense
    Cr Accumulated Depreciation
```

Accounting rules harus configurable per company.

---

# 13. Accounts Receivable

## 13.1 Customer Invoice

Fields:

* invoice_number
* customer_id
* company_id
* branch_id
* invoice_date
* due_date
* currency
* exchange_rate
* payment_term
* subtotal
* discount
* tax
* total
* project_id
* cost_center_id
* status

## 13.2 Features

* Customer invoice
* Credit note
* Debit note
* Down payment
* Customer receipt
* Partial payment
* Payment allocation
* Write-off
* Bad debt
* Collection tracking
* Credit limit
* AR aging
* Customer statement

## 13.3 AR Aging

```text
Current
1–30 Days
31–60 Days
61–90 Days
>90 Days
```

---

# 14. Accounts Payable

## Features

* Vendor bill
* Purchase invoice
* Credit note
* Debit note
* Supplier advance
* Supplier payment
* Partial payment
* Payment allocation
* Payment schedule
* AP aging
* Vendor statement

## 14.1 Three-Way Matching

```text
Purchase Order
      ↓
Goods Receipt
      ↓
Vendor Invoice
      ↓
Accounts Payable
```

Matching berdasarkan:

* Quantity
* Unit price
* Tax
* Supplier
* PO
* Receipt

---

# 15. Cash & Bank

## Bank Account

Fields:

* bank_account_id
* company_id
* bank_name
* account_number
* account_name
* currency
* GL account
* branch
* status

## Features

* Cash receipt
* Cash payment
* Bank receipt
* Bank payment
* Bank transfer
* Intercompany transfer
* Bank charge
* Interest
* Petty cash
* Cash advance

---

# 16. Bank Reconciliation

## Flow

```text
Bank Statement
      ↓
Import
      ↓
Matching Engine
      ↓
Matched / Unmatched
      ↓
Reconciliation
```

## Features

* Import bank statement
* Auto matching
* Manual matching
* Partial matching
* Bank fee
* Outstanding transaction
* Reconciliation report
* Reconciliation history

---

# 17. Inventory Accounting

Untuk perusahaan barang.

## Features

* Inventory valuation
* Goods receipt
* Goods issue
* Stock transfer
* Stock adjustment
* Stock opname
* Sales return
* Purchase return
* COGS
* Landed cost
* Inventory reconciliation

## Costing Methods

MVP:

* Moving Average
* FIFO

## Flow

```text
Purchase
   ↓
Inventory
   ↓
Sales
   ↓
COGS
```

---

# 18. Project Accounting

Untuk perusahaan jasa dan project-based business.

## Features

* Project budget
* Project revenue
* Project expense
* Project cost
* Project billing
* Milestone
* Progress billing
* Retention
* Advance payment
* WIP
* Project profitability

## Project Profitability

```text
Project Revenue
- Direct Cost
- Labor Cost
- Material Cost
- Overhead
= Project Profit
```

---

# 19. Cost Accounting

Transaksi dapat memiliki dimensi:

```text
Company
Branch
Department
Cost Center
Project
Customer
Product
Service
```

## Reporting

* Product profitability
* Service profitability
* Customer profitability
* Project profitability
* Department profitability
* Branch profitability
* Cost center profitability

---

# 20. Fixed Asset

## Asset Register

Fields:

* asset_id
* asset_code
* asset_name
* category
* company_id
* branch_id
* department_id
* acquisition_date
* acquisition_cost
* useful_life
* residual_value
* depreciation_method
* status

## Features

* Asset acquisition
* Capitalization
* Depreciation
* Asset transfer
* Revaluation
* Impairment
* Disposal
* Asset history

---

# 21. Tax

Tax engine harus configurable mengikuti ketentuan perpajakan yang berlaku.

## Features

* Tax master
* Tax code
* Tax rate
* Input VAT
* Output VAT
* Withholding tax
* Tax invoice
* Tax payable
* Tax receivable
* Tax reconciliation
* Tax adjustment
* Tax report/export

Tax configuration dapat berbeda antar-company.

---

# 22. Budgeting

## Budget Structure

```text
Company
 ↓
Branch
 ↓
Department
 ↓
Account
 ↓
Cost Center
 ↓
Project
```

## Features

* Annual budget
* Monthly budget
* Department budget
* Project budget
* Sales budget
* Expense budget
* Cash budget
* Budget revision
* Budget approval
* Forecast

## Budget vs Actual

```text
Budget
Actual
Variance
Variance %
```

---

# 23. Multi-Currency

Setiap company dapat memiliki base currency berbeda.

Contoh:

```text
Company A → IDR
Company B → USD
Company C → SGD
```

## Features

* Transaction currency
* Base currency
* Exchange rate
* Exchange gain
* Exchange loss
* Realized FX
* Unrealized FX
* Currency revaluation

---

# 24. Intercompany

## 24.1 Intercompany Sales

```text
Company A
    │
    │ Sales
    ↓
Company B
```

Company A:

```text
Dr Intercompany Receivable
    Cr Intercompany Revenue
```

Company B:

```text
Dr Inventory / Expense
    Cr Intercompany Payable
```

## 24.2 Features

* Intercompany sales
* Intercompany purchase
* Intercompany expense
* Intercompany loan
* Intercompany transfer
* Intercompany reconciliation
* Intercompany matching
* Intercompany elimination

## 24.3 Matching

```text
Company A Receivable
        =
Company B Payable
```

Perbedaan harus dapat ditampilkan sebagai exception.

---

# 25. Financial Consolidation

## Flow

```text
Company A ─┐
Company B ─┼──→ Consolidation Engine
Company C ─┘             ↓
                  Elimination
                         ↓
              Consolidated Report
```

## Features

* Consolidated P&L
* Consolidated Balance Sheet
* Consolidated Cash Flow
* Consolidated Trial Balance
* Currency translation
* Intercompany elimination
* Consolidation adjustment
* Ownership percentage
* Non-controlling interest

---

# 26. Financial Closing

## Month-End Checklist

* AR reconciliation
* AP reconciliation
* Bank reconciliation
* Inventory reconciliation
* Fixed asset depreciation
* Accrual
* Prepaid expense
* Tax reconciliation
* FX adjustment
* Intercompany reconciliation
* Trial balance review
* P&L review
* Balance Sheet review

## Closing Flow

```text
OPEN
 ↓
CLOSING REVIEW
 ↓
APPROVED
 ↓
CLOSED
```

---

# 27. Financial Reports

## Standard Reports

* Profit & Loss
* Balance Sheet
* Cash Flow
* Trial Balance
* General Ledger
* Journal Report
* AR Aging
* AP Aging
* Customer Statement
* Vendor Statement
* Bank Report
* Tax Report
* Fixed Asset Report
* Inventory Valuation

## Management Reports

* Revenue by company
* Revenue by branch
* Revenue by customer
* Revenue by product
* Revenue by service
* Gross profit
* Project profitability
* Customer profitability
* Cost center profitability
* Budget vs actual
* Cash flow forecast

---

# 28. Multi-Company Reporting

User dapat memilih:

```text
Company      : All Companies
Branch       : All Branches
Period       : Jan–Dec 2026
Currency     : IDR
```

## Example

| Company      | Revenue | Expense | Profit |
| ------------ | ------: | ------: | -----: |
| Company A    |  Rp10 M |   Rp7 M |  Rp3 M |
| Company B    |   Rp8 M |   Rp6 M |  Rp2 M |
| Company C    |   Rp5 M |   Rp4 M |  Rp1 M |
| Consolidated |  Rp23 M |  Rp17 M |  Rp6 M |

Consolidated report harus melakukan elimination atas transaksi intercompany yang memenuhi kriteria eliminasi.

---

# 29. Financial Dashboard

## KPI

* Revenue
* Gross Profit
* Net Profit
* Cash Balance
* AR Outstanding
* AP Outstanding
* Overdue AR
* Overdue AP
* Inventory Value
* COGS
* Operating Expense
* Budget vs Actual
* Project Profitability

## Drill Down

```text
Group
 ↓
Company
 ↓
Branch
 ↓
Department
 ↓
Account
 ↓
Transaction
```

---

# 30. Approval Workflow

## Payment Example

```text
Finance Staff
     ↓
Submit Payment
     ↓
Finance Manager
     ↓
Approved
     ↓
Treasury
     ↓
Paid
     ↓
Posted to GL
```

## Approval Limit

Contoh konfigurasi:

```text
< Rp10 juta
→ Manager

Rp10–100 juta
→ Finance Manager

> Rp100 juta
→ Director
```

Approval limit harus configurable.

---

# 31. Audit Trail

Setiap financial transaction harus mencatat:

* Created by
* Created date
* Updated by
* Updated date
* Submitted by
* Submitted date
* Approved by
* Approved date
* Posted by
* Posted date
* Void by
* Void date
* Previous value
* New value

Financial transaction tidak boleh dihapus secara hard-delete setelah posted.

Gunakan:

```text
Void
Reverse
Adjustment
```

untuk koreksi transaksi.

---

# 32. Attachments

Dokumen dapat dilampirkan pada transaksi:

* Invoice
* Receipt
* Purchase invoice
* Payment
* Journal
* Bank transaction
* Fixed asset
* Tax document
* Project document

Format minimal:

* PDF
* JPG
* PNG
* XLSX
* DOCX

---

# 33. Notifications

System dapat mengirim notification untuk:

* Invoice overdue
* Payment due
* Approval pending
* Budget exceeded
* Bank reconciliation exception
* Intercompany mismatch
* Period closing
* Failed journal posting
* Failed integration

---

# 34. Integration

Accounting harus dapat menerima transaksi dari:

```text
Sales
Purchase
Inventory
Project
Fixed Asset
Payroll
Expense
CRM
POS
Bank
Tax
```

Semua transaksi yang berdampak pada accounting harus masuk melalui accounting engine.

---

# 35. Database Core Entities

Minimal:

```text
groups
companies
branches
departments
cost_centers

users
roles
permissions
user_company_access
user_branch_access

accounts
account_mappings
accounting_periods
journal_entries
journal_lines

customers
customer_invoices
customer_invoice_lines
customer_payments
customer_payment_allocations

suppliers
supplier_bills
supplier_bill_lines
supplier_payments
supplier_payment_allocations

bank_accounts
bank_transactions
bank_reconciliations

products
inventory_transactions
inventory_costs

projects
project_budgets
project_costs
project_billings

fixed_assets
asset_depreciations
asset_transactions

tax_codes
tax_transactions

budgets
budget_lines

intercompany_transactions
intercompany_lines
consolidations
consolidation_adjustments

approval_workflows
approval_requests

attachments
audit_logs
```

---

# 36. Common Transaction Dimensions

Transaction yang relevan dapat memiliki:

```text
company_id
branch_id
department_id
cost_center_id
project_id
customer_id
supplier_id
currency_id
exchange_rate
```

Tidak semua field wajib diisi pada setiap transaksi.

---

# 37. Data Integrity Rules

## Rule 1 — Company Isolation

User hanya dapat mengakses company yang diberikan permission.

## Rule 2 — Posted Journal Immutable

Journal POSTED tidak dapat diedit langsung.

## Rule 3 — Closed Period Immutable

Transaksi tidak dapat diposting ke closed period.

## Rule 4 — Balanced Journal

```text
Total Debit = Total Credit
```

Journal yang tidak balance tidak boleh diposting.

## Rule 5 — Currency

Untuk foreign currency transaction, exchange rate wajib tersedia.

## Rule 6 — Intercompany

Intercompany transaction harus memiliki source company dan target company.

## Rule 7 — Audit

Semua perubahan financial transaction harus memiliki audit trail.

---

# 38. Non-Functional Requirements

## Security

* Role-based access control
* Company-level permission
* Branch-level permission
* MFA support
* Encryption
* Audit log
* Session management

## Performance

Target awal:

```text
Dashboard          < 3 seconds
Standard report    < 5 seconds
Transaction post   < 2 seconds
```

Untuk report besar gunakan asynchronous processing.

## Reliability

* Automated backup
* Point-in-time recovery
* Transaction atomicity
* Journal integrity
* Period locking
* Error recovery

---

# 39. MVP

MVP harus mencakup:

### Core

* Multi-company
* Multi-branch
* User & permission
* COA
* Accounting period
* General Ledger
* Automatic Journal Engine

### Finance

* AR
* AP
* Cash & Bank
* Bank Reconciliation
* Basic Tax
* Fixed Asset

### Operational Accounting

* Inventory Accounting
* Basic Project Accounting
* Cost Center

### Reporting

* P&L
* Balance Sheet
* Cash Flow
* Trial Balance
* GL
* AR Aging
* AP Aging
* Budget vs Actual

### Control

* Approval Workflow
* Audit Trail
* Period Closing

### Currency

* Multi-currency
* FX gain/loss

---

# 40. Phase 2

* Advanced Intercompany
* Consolidation
* Advanced Budgeting
* Forecasting
* Advanced Project Accounting
* Automated Bank Matching
* Cash Flow Forecast
* Advanced Profitability
* Advanced Tax
* Advanced Group Reporting

---

# 41. Phase 3

* AI financial analysis
* Automated reconciliation
* Anomaly detection
* Automated month-end closing
* Financial planning & analysis
* Predictive cash flow
* Intelligent expense classification
* Automated accounting suggestions

---

# 42. Acceptance Criteria

## Multi-company

* [ ] User dapat memiliki akses ke satu atau beberapa company.
* [ ] Data company tidak tercampur.
* [ ] Company dapat memiliki branch sendiri.
* [ ] Company dapat memiliki currency sendiri.
* [ ] Company dapat memiliki COA sendiri.
* [ ] Company dapat memiliki tax configuration sendiri.

## General Ledger

* [ ] Manual journal dapat dibuat.
* [ ] Journal harus balance.
* [ ] Journal dapat melalui approval.
* [ ] Journal dapat diposting.
* [ ] Posted journal tidak dapat diedit langsung.
* [ ] Journal menghasilkan GL entry.

## AR

* [ ] Customer invoice dapat dibuat.
* [ ] Invoice menghasilkan AR.
* [ ] Payment dapat dialokasikan ke invoice.
* [ ] AR aging tersedia.
* [ ] Customer statement tersedia.

## AP

* [ ] Vendor bill dapat dibuat.
* [ ] Bill menghasilkan AP.
* [ ] Payment dapat dialokasikan.
* [ ] AP aging tersedia.
* [ ] Vendor statement tersedia.

## Cash & Bank

* [ ] Bank account dapat dibuat per company.
* [ ] Cash transaction dapat dicatat.
* [ ] Bank transaction dapat dicatat/import.
* [ ] Bank reconciliation dapat dilakukan.

## Inventory

* [ ] Inventory valuation tersedia.
* [ ] COGS otomatis terbentuk.
* [ ] Stock adjustment dapat menghasilkan accounting entry.

## Project

* [ ] Project cost dapat dicatat.
* [ ] Project revenue dapat dicatat.
* [ ] Project profitability tersedia.

## Intercompany

* [ ] Company A dapat bertransaksi dengan Company B.
* [ ] AR/AP intercompany dapat terbentuk.
* [ ] Intercompany dapat direkonsiliasi.
* [ ] Intercompany dapat dieliminasi saat consolidation.

## Reporting

* [ ] Report dapat difilter berdasarkan company.
* [ ] Report dapat difilter berdasarkan branch.
* [ ] Report dapat difilter berdasarkan period.
* [ ] Consolidated report tersedia.
* [ ] Financial report mengambil data dari GL.

---

# 43. Key Business Flows

## Sales to Cash

```text
Quotation
    ↓
Sales Order
    ↓
Delivery
    ↓
Sales Invoice
    ↓
AR
    ↓
Customer Payment
    ↓
Bank
    ↓
GL
```

## Procure to Pay

```text
Purchase Request
    ↓
Purchase Order
    ↓
Goods Receipt
    ↓
Vendor Invoice
    ↓
AP
    ↓
Supplier Payment
    ↓
Bank
    ↓
GL
```

## Project to Cash

```text
Project
    ↓
Project Budget
    ↓
Project Cost
    ↓
Project Progress
    ↓
Project Billing
    ↓
AR
    ↓
Customer Payment
    ↓
GL
```

## Intercompany

```text
Company A
    ↓
Intercompany Sales
    ↓
Company B
    ↓
Intercompany Purchase
    ↓
Matching
    ↓
Elimination
    ↓
Consolidation
```

---

# 44. Success Metrics

Produk dianggap berhasil apabila:

1. Mayoritas journal berasal dari transaksi otomatis.
2. Tidak ada transaksi antar-company yang tidak teridentifikasi.
3. AR dan AP dapat direkonsiliasi.
4. Bank reconciliation dapat dilakukan secara berkala.
5. Month-end closing dapat dilakukan dengan workflow terkontrol.
6. Financial report dapat dihasilkan berdasarkan company.
7. Consolidated report dapat mengeliminasi transaksi intercompany.
8. Semua posted financial transaction memiliki audit trail.
9. User hanya dapat mengakses data sesuai permission.
10. Balance Debit dan Credit selalu terjaga.

---

# 45. Recommended Menu Structure

```text
ACCOUNTING & FINANCE
│
├── Dashboard
│
├── General Ledger
│   ├── Chart of Accounts
│   ├── Journal Entry
│   ├── Recurring Journal
│   ├── Accrual
│   ├── Reclassification
│   ├── Trial Balance
│   ├── General Ledger
│   └── Period Closing
│
├── Accounts Receivable
│   ├── Customer Invoice
│   ├── Credit Note
│   ├── Debit Note
│   ├── Customer Payment
│   ├── AR Aging
│   └── Customer Statement
│
├── Accounts Payable
│   ├── Vendor Bill
│   ├── Credit Note
│   ├── Debit Note
│   ├── Supplier Payment
│   ├── AP Aging
│   └── Vendor Statement
│
├── Cash & Bank
│   ├── Bank Accounts
│   ├── Cash Receipt
│   ├── Cash Payment
│   ├── Bank Transfer
│   ├── Petty Cash
│   └── Bank Reconciliation
│
├── Inventory Accounting
│   ├── Inventory Valuation
│   ├── COGS
│   ├── Stock Adjustment
│   ├── Stock Opname
│   └── Landed Cost
│
├── Project Accounting
│   ├── Project Budget
│   ├── Project Cost
│   ├── Project Billing
│   ├── WIP
│   └── Project Profitability
│
├── Fixed Assets
│   ├── Asset Register
│   ├── Acquisition
│   ├── Depreciation
│   ├── Transfer
│   └── Disposal
│
├── Tax
│   ├── Tax Codes
│   ├── VAT / PPN
│   ├── Withholding Tax
│   ├── Tax Reconciliation
│   └── Tax Report
│
├── Budgeting
│   ├── Budget
│   ├── Forecast
│   └── Budget vs Actual
│
├── Intercompany
│   ├── Transactions
│   ├── Matching
│   ├── Reconciliation
│   └── Elimination
│
├── Consolidation
│   ├── Consolidation Setup
│   ├── Consolidation Run
│   ├── Adjustments
│   └── Consolidated Reports
│
└── Reports
    ├── Profit & Loss
    ├── Balance Sheet
    ├── Cash Flow
    ├── Trial Balance
    ├── General Ledger
    ├── AR
    ├── AP
    ├── Inventory
    ├── Tax
    ├── Fixed Asset
    ├── Project
    └── Management Reports
```

---

# 46. Final Architecture Principle

Accounting & Finance harus menjadi **central financial engine**.

```text
                         ┌─────────────┐
                         │    GROUP    │
                         └──────┬──────┘
                                │
               ┌────────────────┼────────────────┐
               │                │                │
               ▼                ▼                ▼
          Company A        Company B        Company C
               │                │                │
       ┌───────┼───────┐        │        ┌───────┼───────┐
       ▼       ▼       ▼        ▼        ▼       ▼       ▼
     Sales  Purchase Inventory Project  Sales  Purchase Project
       │       │       │        │        │       │       │
       └───────┴───────┴────────┴────────┴───────┴───────┘
                                │
                                ▼
                    ┌────────────────────┐
                    │ ACCOUNTING ENGINE  │
                    └─────────┬──────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
       AR/AP              Cash & Bank              GL
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              ▼
                     Financial Reporting
                              │
                              ▼
                     Consolidation Engine
                              │
                              ▼
                      GROUP REPORTING
```

**Prinsip utama:** seluruh transaksi finansial harus memiliki `company_id` sebagai minimum organizational boundary, sedangkan transaksi yang membutuhkan analisis lebih detail dapat menggunakan `branch_id`, `department_id`, `cost_center_id`, dan `project_id`. Journal yang sudah `POSTED` tidak boleh diedit secara langsung; koreksi dilakukan melalui reversal/adjustment sehingga audit trail dan integritas laporan tetap terjaga.
