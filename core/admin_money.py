"""Format angka jadi currency di halaman admin (changelist + form readonly).

Django punya dua jalur render angka, jadi dua patch:

1. Model field (DecimalField) -> `display_for_field`. Nama field di model
   konsisten, jadi cukup kecualikan rate/kuantitas/persen.
2. Kolom method/property (mis. `total_debit`) -> `display_for_value`, yang tidak
   menerima nama kolom. Yang property tidak bisa dipastikan tipenya dari nama
   saja (mis. `available` di inventory itu quantity), jadi di sini pakai daftar
   positif: format hanya kalau namanya jelas nominal. Salah label uang jauh lebih
   berbahaya daripada tampil mentah.
"""
import re
from decimal import Decimal

from django.conf import settings
from django.contrib.admin import helpers
from django.contrib.admin import utils as admin_utils
from django.contrib.admin.templatetags import admin_list
from django.contrib.humanize.templatetags.humanize import intcomma
from django.db import models

# Field model: yang BUKAN uang (kurs, rasio, jumlah, persen).
NON_MONEY = re.compile(
    r'(^|_)(rate|ratio|quantity|qty|percent|percentage|ownership|'
    r'factor|multiplier|hours|days|count|number|seq)(_|$)',
    re.I,
)

# Kolom method/property: hanya yang jelas nominal.
MONEY_HINT = re.compile(
    r'(debit|credit|amount|balance|price|cost|value|subtotal|paid|'
    r'tax|fee|discount|gross|net|revenue|expense|valuation)',
    re.I,
)


def is_money_field(field_name):
    """Kolom DecimalField milik model: uang kecuali rate/kuantitas/persen."""
    return bool(field_name) and not NON_MONEY.search(field_name)


def is_money_computed(column_name):
    """Kolom method/property: hanya format kalau namanya jelas nominal."""
    return bool(column_name) and bool(MONEY_HINT.search(column_name))


def money(value, prefix=None, places=2):
    """100000000.00 -> 'Rp 100,000,000'; desimal hanya tampil kalau bukan nol."""
    if prefix is None:
        prefix = getattr(settings, 'ADMIN_CURRENCY_PREFIX', 'Rp ')
    amount = Decimal(str(value))
    sign = '-' if amount < 0 else ''
    amount = abs(amount)
    if amount == amount.to_integral_value():
        body = intcomma(int(amount))
    else:
        body = intcomma(amount.quantize(Decimal(1).scaleb(-places)))
    return f'{sign}{prefix}{body}'


def install():
    """Pasang patch. Idempoten."""
    if getattr(admin_list.display_for_field, '_admin_money_patch', False):
        return

    original_field = admin_list.display_for_field
    original_lookup = admin_list.lookup_field

    def display_for_field(value, field, empty_value_display):
        if isinstance(field, models.DecimalField) and is_money_field(field.name) and value is not None:
            return money(value, places=field.decimal_places)
        return original_field(value, field, empty_value_display)

    def lookup_field(name, obj, model_admin):
        field, attr, value = original_lookup(name, obj, model_admin)
        # Kolom tanpa field model (property/method) + nilai Decimal -> format di sini,
        # karena display_for_value tidak tahu nama kolomnya.
        if field is None or field.auto_created:
            if isinstance(value, Decimal) and is_money_computed(name):
                return field, attr, money(value)
        return field, attr, value

    display_for_field._admin_money_patch = True
    for module in (admin_list, helpers, admin_utils):
        module.display_for_field = display_for_field
    admin_list.lookup_field = lookup_field