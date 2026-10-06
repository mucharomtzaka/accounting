from decimal import Decimal, InvalidOperation

from django import template
from django.contrib.humanize.templatetags.humanize import intcomma

register = template.Library()


@register.filter
def money(value, currency='Rp'):
    """Format: 1234567.89 -> Rp 1,234,568. Desimal dibuang agar ringkas di KPI/chart."""
    if value is None:
        return '-'
    try:
        amount = round(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return value
    sign = '-' if amount < 0 else ''
    return f'{sign}{currency} {intcomma(abs(amount))}'


@register.filter
def money_compact(value, currency='Rp'):
    """Format ringkas: 1_234_567 -> Rp 1.2 jt. Untuk label chart."""
    if value is None:
        return '-'
    try:
        amount = float(value)
    except (ValueError, TypeError):
        return value
    sign = '-' if amount < 0 else ''
    amount = abs(amount)
    if amount >= 1_000_000_000:
        short = f'{amount / 1_000_000_000:g} M'
    elif amount >= 1_000_000:
        short = f'{amount / 1_000_000:g} jt'
    elif amount >= 1_000:
        short = f'{amount / 1_000:g} rb'
    else:
        short = intcomma(round(amount))
    return f'{sign}{currency} {short}'
