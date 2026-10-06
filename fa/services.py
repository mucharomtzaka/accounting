"""PRD 20 - Fixed Asset services: acquisition, depreciation, disposal, transfer."""
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from gl.engine import JournalBuilder, get_account, get_period
from gl.models import JournalSource

from .models import AssetDepreciation, AssetStatus, AssetTransaction, FixedAsset


def next_asset_number(company):
    from django.utils import timezone

    prefix = f'FA/{timezone.localdate():%Y}/'
    last = (
        FixedAsset.objects.filter(company=company, asset_code__startswith=prefix)
        .order_by('-asset_code').values_list('asset_code', flat=True).first()
    )
    seq = int(last.rsplit('/', 1)[1]) + 1 if last else 1
    return f'{prefix}{seq:04d}'


def acquire_asset(asset, user=None):
    if asset.status != AssetStatus.DRAFT:
        raise ValidationError(f'Asset {asset.asset_code} sudah di-acquire.')
    if asset.acquisition_cost < asset.residual_value:
        raise ValidationError('Residual value tidak boleh lebih besar dari acquisition cost.')

    asset.status = AssetStatus.ACTIVE
    asset.start_depreciation_date = asset.start_depreciation_date or asset.acquisition_date
    asset.accumulated_depreciation = Decimal('0')
    asset.net_book_value = asset.acquisition_cost
    asset.save(update_fields=[
        'status', 'start_depreciation_date', 'accumulated_depreciation', 'net_book_value',
    ])
    AssetTransaction.objects.create(
        asset=asset, tx_type='ACQUISITION', transaction_date=asset.acquisition_date,
        amount=asset.acquisition_cost, description=f'Acquisition {asset.asset_code}',
        journal=asset.acquisition_journal,
    )
    return asset


def depreciate_asset(asset, depreciation_date=None, user=None, notes=''):
    """Straight line, satu kali per period (unique constraint asset+period)."""
    if asset.status == AssetStatus.FULLY_DEPRECIATED:
        raise ValidationError(f'Asset {asset.asset_code} sudah fully depreciated.')
    if asset.status == AssetStatus.DISPOSED:
        raise ValidationError(f'Asset {asset.asset_code} sudah disposed.')
    if asset.status != AssetStatus.ACTIVE:
        raise ValidationError(f'Asset status {asset.get_status_display()} tidak bisa didepresiasi.')

    depreciation_date = depreciation_date or date.today()
    if asset.start_depreciation_date and depreciation_date < asset.start_depreciation_date:
        raise ValidationError('Tanggal depresiasi sebelum start depreciation date.')

    remaining = asset.depreciable_base - asset.accumulated_depreciation
    if remaining <= Decimal('0.01'):
        asset.status = AssetStatus.FULLY_DEPRECIATED
        asset.save(update_fields=['status'])
        raise ValidationError(f'Asset {asset.asset_code} sudah fully depreciated.')

    amount = min(asset.annual_depreciation, remaining)

    period = get_period(asset.company, depreciation_date)
    if period.is_locked:
        raise ValidationError(f'Period {period.period_code} berstatus CLOSED.')
    if AssetDepreciation.objects.filter(asset=asset, period=period).exists():
        raise ValidationError(
            f'{asset.asset_code} sudah didepresiasi di period {period.period_code}.'
        )

    with transaction.atomic():
        accumulated_before = asset.accumulated_depreciation
        nbv_before = asset.net_book_value

        entry = AssetDepreciation.objects.create(
            asset=asset, period=period, depreciation_date=depreciation_date,
            depreciation_amount=amount, accumulated_before=accumulated_before,
            accumulated_after=accumulated_before + amount,
            net_book_value_before=nbv_before, net_book_value_after=nbv_before - amount,
            notes=notes,
        )

        category = asset.asset_category
        journal = (
            JournalBuilder(
                asset.company, depreciation_date, source=JournalSource.ASSET,
                description=f'Depreciation {asset.asset_code} - {asset.asset_name}',
                branch=asset.branch, department=asset.department,
                cost_center=asset.cost_center, project=asset.project,
                reference=f'DEP/{asset.asset_code}', user=user,
            )
            .debit(category.depreciation_account, amount, f'Depreciation - {asset.asset_name}')
            .credit(category.accumulated_account, amount, f'Akumulasi Penyusutan - {asset.asset_name}')
            .save(auto_post=True)
        )

        entry.journal = journal
        entry.save(update_fields=['journal'])

        asset.accumulated_depreciation = accumulated_before + amount
        asset.net_book_value = nbv_before - amount
        asset.last_depreciation_date = depreciation_date
        if asset.accumulated_depreciation >= asset.depreciable_base:
            asset.status = AssetStatus.FULLY_DEPRECIATED
            asset.net_book_value = asset.residual_value
        asset.save(update_fields=[
            'accumulated_depreciation', 'net_book_value', 'last_depreciation_date', 'status',
        ])

        AssetTransaction.objects.create(
            asset=asset, tx_type='DEPRECIATION', transaction_date=depreciation_date,
            amount=amount, description=notes or 'Depreciation', journal=journal,
        )
    return entry


def dispose_asset(asset, disposal_date=None, proceeds=Decimal('0'), bank_account=None, user=None, notes=''):
    """Dr Akumulasi + Dr Kas / Cr Aset, selisih ke akun gain/loss."""
    if asset.status == AssetStatus.DISPOSED:
        raise ValidationError(f'Asset {asset.asset_code} sudah disposed.')
    if asset.status not in (AssetStatus.ACTIVE, AssetStatus.FULLY_DEPRECIATED):
        raise ValidationError(f'Asset status {asset.get_status_display()} tidak bisa di-dispose.')

    disposal_date = disposal_date or date.today()
    period = get_period(asset.company, disposal_date)
    if period.is_locked:
        raise ValidationError(f'Period {period.period_code} berstatus CLOSED.')

    proceeds = Decimal(proceeds or 0)
    with transaction.atomic():
        cost = asset.acquisition_cost
        accumulated = asset.accumulated_depreciation
        book_value = cost - accumulated
        gain_loss = proceeds - book_value
        category = asset.asset_category

        builder = JournalBuilder(
            asset.company, disposal_date, source=JournalSource.ASSET,
            description=f'Disposal {asset.asset_code} - {asset.asset_name}',
            branch=asset.branch, department=asset.department,
            cost_center=asset.cost_center, project=asset.project,
            reference=f'DISP/{asset.asset_code}', user=user,
        )
        if accumulated:
            builder.debit(category.accumulated_account, accumulated, 'Akumulasi Penyusutan - disposal')
        if cost:
            builder.credit(category.asset_account, cost, 'Aset Tetap - disposal')
        if proceeds:
            cash = bank_account.gl_account if bank_account else get_account(
                asset.company, 'BANK', 'BANK_DEFAULT', required=False
            )
            if not cash:
                raise ValidationError('Bank account untuk pemasukan disposal belum ditentukan.')
            builder.debit(cash, proceeds, 'Hasil disposal')
        if gain_loss:
            account = (category.disposal_gain_account if gain_loss > 0
                       else category.disposal_loss_account)
            if not account:
                account = get_account(
                    asset.company, 'ASSET',
                    'GAIN_ON_DISPOSAL' if gain_loss > 0 else 'LOSS_ON_DISPOSAL',
                )
            if gain_loss > 0:
                builder.credit(account, gain_loss, 'Keuntungan disposal')
            else:
                builder.debit(account, -gain_loss, 'Kerugian disposal')

        journal = builder.save(auto_post=True)

        asset.status = AssetStatus.DISPOSED
        asset.accumulated_depreciation = accumulated
        asset.net_book_value = Decimal('0')
        asset.disposal_journal = journal
        asset.save(update_fields=['status', 'accumulated_depreciation', 'net_book_value', 'disposal_journal'])

        AssetTransaction.objects.create(
            asset=asset, tx_type='DISPOSAL', transaction_date=disposal_date,
            amount=proceeds, description=notes or 'Asset disposal', journal=journal,
        )
    return journal


def transfer_asset(asset, to_branch=None, to_department=None, to_cost_center=None,
                   to_project=None, transfer_date=None, notes=''):
    """Transfer lokasi - mengubah dimensi, tidak menghasilkan jurnal (PRD 20)."""
    if asset.status == AssetStatus.DISPOSED:
        raise ValidationError('Asset yang sudah disposed tidak bisa dipindahkan.')

    changed = []
    if to_branch and to_branch != asset.branch:
        asset.branch = to_branch
        changed.append(f'branch -> {to_branch.branch_code}')
    if to_department and to_department != asset.department:
        asset.department = to_department
        changed.append(f'department -> {to_department.department_code}')
    if to_cost_center and to_cost_center != asset.cost_center:
        asset.cost_center = to_cost_center
        changed.append(f'cost center -> {to_cost_center.cost_center_code}')
    if to_project and to_project != asset.project:
        asset.project = to_project
        changed.append(f'project -> {to_project.project_code}')

    if not changed:
        return asset

    asset.save(update_fields=['branch', 'department', 'cost_center', 'project', 'updated_at'])
    AssetTransaction.objects.create(
        asset=asset, tx_type='TRANSFER', transaction_date=transfer_date or date.today(),
        amount=Decimal('0'), description=notes or '; '.join(changed),
    )
    return asset