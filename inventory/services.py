"""PRD 17 - Inventory costing. MVP: Moving Average + FIFO."""
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    CostingMethod, Inventory, InventoryCost, InventoryTransaction, StockOpname,
)


def get_or_create_inventory(product):
    inv, _ = Inventory.objects.get_or_create(
        company=product.company, product=product,
        defaults={'costing_method': CostingMethod.MOVING_AVERAGE},
    )
    return inv


@transaction.atomic
def goods_receipt(product, quantity, unit_cost, reference='', description='',
                  transaction_date=None, posting_journal=None):
    quantity = Decimal(quantity)
    unit_cost = Decimal(unit_cost)
    if quantity <= 0:
        raise ValidationError('Quantity goods receipt harus lebih besar dari 0.')
    if unit_cost < 0:
        raise ValidationError('Unit cost tidak boleh negatif.')

    inv = get_or_create_inventory(product)
    tx = InventoryTransaction.objects.create(
        inventory=inv, product=product, tx_type=InventoryTransaction.TxType.GR,
        transaction_date=transaction_date or timezone.localdate(),
        quantity=quantity, unit_cost=unit_cost,
        total_cost=(quantity * unit_cost).quantize(Decimal('0.01')),
        reference=reference, description=description, journal=posting_journal, is_posted=True,
    )

    if inv.costing_method == CostingMethod.MOVING_AVERAGE:
        new_qoh = inv.quantity_on_hand + quantity
        new_total = inv.total_cost + tx.total_cost
        inv.average_cost = (new_total / new_qoh).quantize(Decimal('0.01')) if new_qoh > 0 else unit_cost
        inv.total_cost = new_total
        inv.quantity_on_hand = new_qoh
        inv.valuation = (new_qoh * inv.average_cost).quantize(Decimal('0.01'))
    else:
        # FIFO: setiap masuk = satu layer
        inv.quantity_on_hand += quantity
        inv.total_cost += tx.total_cost
        inv.valuation = inv.total_cost
        inv.average_cost = (inv.total_cost / inv.quantity_on_hand).quantize(Decimal('0.01'))

    inv.last_cost = unit_cost
    inv.save(update_fields=[
        'quantity_on_hand', 'average_cost', 'last_cost', 'total_cost', 'valuation', 'updated_at',
    ])
    InventoryCost.objects.create(
        inventory=inv, transaction=tx, quantity_in=quantity, quantity_balance=inv.quantity_on_hand,
        unit_cost=unit_cost, total_cost_balance=inv.total_cost, method=inv.costing_method,
    )
    return tx, inv


@transaction.atomic
def goods_issue(product, quantity, reference='', description='', transaction_date=None,
                posting_journal=None):
    quantity = Decimal(quantity)
    if quantity <= 0:
        raise ValidationError('Quantity goods issue harus lebih besar dari 0.')

    inv = get_or_create_inventory(product)
    if inv.quantity_on_hand < quantity:
        raise ValidationError(
            f'Stock tidak cukup untuk {product.product_code}: '
            f'sedang ada {inv.quantity_on_hand}, diminta {quantity}.'
        )

    unit_cost = Decimal('0')
    if inv.costing_method == CostingMethod.MOVING_AVERAGE:
        unit_cost = inv.average_cost
    else:
        # FIFO: pakai layer termurah/terlama
        layers = inv.cost_layers.filter(quantity_balance__gt=0).order_by('id')
        remaining = quantity
        unit_cost = inv.average_cost or Decimal('0')
        for layer in layers:
            take = min(remaining, layer.quantity_balance)
            unit_cost = layer.unit_cost
            remaining -= take
            if remaining <= 0:
                break

    total_cost = (quantity * unit_cost).quantize(Decimal('0.01'))
    tx = InventoryTransaction.objects.create(
        inventory=inv, product=product, tx_type=InventoryTransaction.TxType.GI,
        transaction_date=transaction_date or timezone.localdate(),
        quantity=quantity, unit_cost=unit_cost, total_cost=total_cost,
        reference=reference, description=description, journal=posting_journal, is_posted=True,
    )

    inv.quantity_on_hand -= quantity
    if inv.costing_method == CostingMethod.MOVING_AVERAGE:
        inv.total_cost = (inv.quantity_on_hand * inv.average_cost).quantize(Decimal('0.01'))
    else:
        inv.total_cost = (inv.total_cost - total_cost).quantize(Decimal('0.01'))
    inv.valuation = inv.total_cost
    if inv.quantity_on_hand <= 0:
        inv.average_cost = Decimal('0')
        inv.total_cost = Decimal('0')
        inv.valuation = Decimal('0')
    inv.save(update_fields=[
        'quantity_on_hand', 'average_cost', 'total_cost', 'valuation', 'updated_at',
    ])
    InventoryCost.objects.create(
        inventory=inv, transaction=tx, quantity_out=quantity, quantity_balance=inv.quantity_on_hand,
        unit_cost=unit_cost, total_cost_balance=inv.total_cost, method=inv.costing_method,
    )
    return tx, inv, total_cost


@transaction.atomic
def stock_adjustment(product, delta, reference='ADJ', description='',
                     transaction_date=None, user=None):
    """Delta positif = masuk (pake average/last cost), negatif = keluar."""
    delta = Decimal(delta)
    if delta == 0:
        raise ValidationError('Delta adjustment tidak boleh 0.')

    inv = get_or_create_inventory(product)
    tx_date = transaction_date or timezone.localdate()

    if delta > 0:
        unit_cost = inv.last_cost or inv.average_cost or product.cost
        tx, inv = goods_receipt(
            product, delta, unit_cost, reference=reference, description=description,
            transaction_date=tx_date,
        )
        tx.tx_type = InventoryTransaction.TxType.ADJUSTMENT_IN
        tx.save(update_fields=['tx_type'])
    else:
        tx, inv, _ = goods_issue(
            product, abs(delta), reference=reference, description=description,
            transaction_date=tx_date,
        )
        tx.tx_type = InventoryTransaction.TxType.ADJUSTMENT_OUT
        tx.save(update_fields=['tx_type'])
    return tx, inv


@transaction.atomic
def refresh_system_quantity(opname):
    """Snapshot qty & cost sistem ke semua baris opname.

    Dipanggil saat sesi mulai dihitung: qty sistem harus dibekukan supaya
    variance tidak berubah gara-gara transaksi lain setelah penghitungan.
    """
    for line in opname.lines.select_related('inventory'):
        inv = line.inventory
        line.system_quantity = inv.quantity_on_hand
        line.unit_cost = inv.average_cost or inv.last_cost
        line.save(update_fields=['system_quantity', 'unit_cost', 'updated_at'])
    return opname


@transaction.atomic
def post_stock_opname(opname, user=None):
    """Terapkan variance setiap baris sebagai stock adjustment, lalu kunci dokumen."""
    if opname.status == StockOpname.Status.POSTED:
        raise ValidationError('Stock opname sudah diposting.')
    if opname.status == StockOpname.Status.CANCELLED:
        raise ValidationError('Stock opname sudah dibatalkan.')
    if not opname.lines.exists():
        raise ValidationError('Stock opname tanpa baris tidak bisa diposting.')

    for line in opname.lines.select_related('inventory__product'):
        delta = line.variance
        if delta:
            stock_adjustment(
                line.inventory.product, delta,
                reference=opname.opname_number,
                description=f'Stock opname {opname.opname_number}',
                transaction_date=opname.opname_date, user=user,
            )

    opname.status = StockOpname.Status.POSTED
    opname.posted_by = user
    opname.posted_at = timezone.now()
    opname.save(update_fields=['status', 'posted_by', 'posted_at', 'updated_at'])
    return opname


def inventory_valuation(company, as_of=None):
    """PRD 27 - Inventory Valuation report."""
    qs = Inventory.objects.filter(company=company, is_active=True)
    rows = [{
        'inventory': inv,
        'product': inv.product,
        'quantity_on_hand': inv.quantity_on_hand,
        'average_cost': inv.average_cost,
        'total_cost': inv.total_cost,
        'valuation': inv.valuation,
    } for inv in qs]
    return rows, sum(r['valuation'] for r in rows)