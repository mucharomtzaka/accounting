"""PRD 9.3 - COA template per company (Indonesian PSAK-based structure)."""
from decimal import Decimal

COA_TEMPLATE = [
    # (code, name, category, type, normal_balance, postable, control, recon)
    ('1000', 'ASET', 1, 'ASSET', 'D', False, False, False),
    ('1100', 'Kas & Bank', 1, 'CASH_BANK', 'D', False, False, False),
    ('1101', 'Kas Tunai', 1, 'CASH', 'D', True, True, False),
    ('1102', 'Bank BCA', 1, 'BANK', 'D', True, True, True),
    ('1103', 'Bank Mandiri', 1, 'BANK', 'D', True, True, True),
    ('1110', 'Petty Cash', 1, 'PETTY_CASH', 'D', True, True, False),
    ('1200', 'Piutang Usaha', 1, 'ACCOUNT_RECEIVABLE', 'D', False, False, False),
    ('1201', 'Piutang Usaha', 1, 'ACCOUNT_RECEIVABLE', 'D', True, True, True),
    ('1202', 'Piutang Intercompany', 1, 'INTERCOMPANY_RECEIVABLE', 'D', True, True, True),
    ('1203', 'Piutang Retensi Proyek', 1, 'RETENTION_RECEIVABLE', 'D', True, True, False),
    ('1290', 'Piutang Lain-lain', 1, 'OTHER_RECEIVABLE', 'D', True, True, False),
    ('1300', 'Persediaan', 1, 'INVENTORY', 'D', False, False, False),
    ('1301', 'Persediaan Barang', 1, 'INVENTORY', 'D', True, True, True),
    ('1302', 'Persediaan Jasa dalam Proses (WIP)', 1, 'WIP', 'D', True, True, False),
    ('1400', 'Aset Tetap', 1, 'FIXED_ASSET', 'D', False, False, False),
    ('1401', 'Aset Tetap - Perolehan', 1, 'FIXED_ASSET', 'D', True, True, False),
    ('1402', 'Akumulasi Penyusutan', 1, 'CONTRA_ASSET', 'C', True, True, False),
    ('1500', 'Aset Lain-lain', 1, 'OTHER_ASSET', 'D', True, False, False),
    ('1390', 'Penyesuaian Selisih Kurs (Unrealized)', 1, 'FX_ADJUST', 'D', True, False, False),

    ('2000', 'KEWAJIBAN', 2, 'LIABILITY', 'C', False, False, False),
    ('2100', 'Utang Usaha', 2, 'ACCOUNT_PAYABLE', 'C', False, False, False),
    ('2101', 'Utang Usaha', 2, 'ACCOUNT_PAYABLE', 'C', True, True, True),
    ('2102', 'Utang Intercompany', 2, 'INTERCOMPANY_PAYABLE', 'C', True, True, True),
    ('2200', 'Pajak', 2, 'TAX', 'C', False, False, False),
    ('2201', 'PPN Masukan (Input VAT)', 1, 'INPUT_VAT', 'D', True, True, True),
    ('2202', 'PPN Keluaran (Output VAT)', 2, 'OUTPUT_VAT', 'C', True, True, True),
    ('2203', 'Pajak Peng-potong', 2, 'WITHHOLDING', 'C', True, True, False),
    ('2300', 'Beban Belum Dibayar (Accrued)', 2, 'ACCRUED', 'C', True, True, False),

    ('3000', 'EKUITAS', 3, 'EQUITY', 'C', False, False, False),
    ('3101', 'Modal Disetor', 3, 'PAID_IN_CAPITAL', 'C', True, True, False),
    ('3201', 'Laba Ditahan', 3, 'RETAINED_EARNINGS', 'C', True, True, False),
    ('3301', 'Devaluasi Rupiah', 3, 'FX_TRANSLATION', 'C', True, False, False),

    ('4000', 'PENDAPATAN', 4, 'REVENUE', 'C', False, False, False),
    ('4101', 'Pendapatan Penjualan Produk', 4, 'PRODUCT_REVENUE', 'C', True, True, False),
    ('4201', 'Pendapatan Jasa', 4, 'SERVICE_REVENUE', 'C', True, True, False),
    ('4301', 'Pendapatan Intercompany', 4, 'INTERCOMPANY_REVENUE', 'C', True, True, False),
    ('4310', 'Keuntungan Penjualan Aset Tetap', 4, 'GAIN_ON_DISPOSAL', 'C', True, True, False),
    ('4320', 'Keuntungan Selisih Kurs Belum Terealisasi', 4, 'FX_GAIN', 'C', True, True, False),
    ('4901', 'Diskon Penjualan', 4, 'DISCOUNT', 'D', True, False, False),

    ('5000', 'HARGA POKOK PENJUALAN', 5, 'COGS', 'D', False, False, False),
    ('5101', 'Harga Pokok Penjualan Produk', 5, 'PRODUCT_COGS', 'D', True, True, False),
    ('5201', 'Harga Pokok Penjualan Jasa', 5, 'SERVICE_COST', 'D', True, True, False),

    ('6000', 'BEBAN OPERASIONAL', 6, 'EXPENSE', 'D', False, False, False),
    ('6101', 'Beban Administrasi & Umum', 6, 'GENERAL_EXPENSE', 'D', True, False, False),
    ('6201', 'Beban Gaji', 6, 'SALARY_EXPENSE', 'D', True, False, False),
    ('6301', 'Beban Pemasaran', 6, 'MARKETING_EXPENSE', 'D', True, False, False),
    ('6401', 'Beban Penyusutan', 6, 'DEPRECIATION_EXPENSE', 'D', True, True, False),
    ('6501', 'Biaya Bank', 6, 'BANK_CHARGE', 'D', True, True, False),
    ('6601', 'Beban Sewa', 6, 'RENT_EXPENSE', 'D', True, False, False),
    ('6701', 'Beban Utilitas', 6, 'UTILITY_EXPENSE', 'D', True, False, False),
    ('6801', 'Beban Transportasi', 6, 'TRANSPORT_EXPENSE', 'D', True, False, False),
    ('6901', 'Beban Lain-lain', 6, 'OTHER_EXPENSE', 'D', True, False, False),
    ('6910', 'Kerugian Penjualan Aset Tetap', 6, 'LOSS_ON_DISPOSAL', 'D', True, True, False),
    ('6920', 'Kerugian Selisih Kurs Belum Terealisasi', 6, 'FX_LOSS', 'D', True, True, False),
]

ACCOUNT_MAPPINGS = [
    ('AR', 'AR_CONTROL', '1201'),
    ('AR', 'AR_INTERCO', '1202'),
    ('AP', 'AP_CONTROL', '2101'),
    ('AP', 'AP_INTERCO', '2102'),
    ('TAX', 'VAT_INPUT', '2201'),
    ('TAX', 'VAT_OUTPUT', '2202'),
    ('TAX', 'WITHHOLDING_PAYABLE', '2203'),
    ('BANK', 'BANK_CHARGE', '6501'),
    ('SALES', 'DISCOUNT', '4901'),
    ('INVENTORY', 'INVENTORY_ASSET', '1301'),
    ('INVENTORY', 'WIP', '1302'),
    ('INTERCOMPANY', 'IC_REVENUE', '4301'),
    ('INTERCOMPANY', 'IC_RECEIVABLE', '1202'),
    ('INTERCOMPANY', 'IC_PAYABLE', '2102'),
    ('INTERCOMPANY', 'IC_EXPENSE', '6101'),
    ('COGS', 'PRODUCT_COGS', '5101'),
    ('BANK', 'BANK_DEFAULT', '1102'),
    ('ASSET', 'GAIN_ON_DISPOSAL', '4310'),
    ('ASSET', 'LOSS_ON_DISPOSAL', '6910'),
    ('PROJECT', 'WIP', '1302'),
    ('PROJECT', 'REVENUE', '4201'),
    ('PROJECT', 'RETENTION', '1203'),
    ('PROJECT', 'LABOR', '6201'),
    ('PROJECT', 'MATERIAL', '6901'),
    ('PROJECT', 'OVERHEAD', '6101'),
    ('TAX', 'FX_GAIN', '4320'),
    ('TAX', 'FX_LOSS', '6920'),
    ('TAX', 'FX_ADJUST', '1390'),
]


def seed_coa(company, mappings=True):
    """Buat COA per company. Return dict code -> Account."""
    from gl.models import Account, AccountMapping

    accounts = {}
    for code, name, category, acc_type, normal, postable, control, recon in COA_TEMPLATE:
        parent_code = code[:-2] + '00'
        parent = accounts.get(parent_code) if len(code) > 4 else None
        account, _ = Account.objects.update_or_create(
            company=company, account_code=code,
            defaults={
                'account_name': name,
                'account_category': category,
                'account_type': acc_type,
                'parent': parent,
                'normal_balance': normal,
                'is_postable': postable,
                'is_control_account': control,
                'is_reconciliation_account': recon,
                'is_active': True,
            },
        )
        accounts[code] = account

    if mappings:
        for module, key, code in ACCOUNT_MAPPINGS:
            AccountMapping.objects.update_or_create(
                company=company, module=module, key=key,
                defaults={'account': accounts[code], 'is_active': True},
            )
    return accounts