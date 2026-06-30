import math as _math
from decimal import Decimal
from django import template

register = template.Library()

_TYPE_LABELS = {
    'ASSET': 'Assets',
    'LIABILITY': 'Liabilities',
    'INCOME': 'Income',
    'EXPENSE': 'Expenses',
    'EQUITY': 'Equity',
}

@register.filter
def account_type_label(value):
    return _TYPE_LABELS.get(value, value.title())


@register.filter
def amt(value, prefs):
    """Format a number using the user's currency preferences.
    Usage: {{ value|amt:prefs }}
    """
    if prefs is None or value is None:
        return value
    try:
        v = Decimal(str(value))
    except Exception:
        return value

    if prefs.decimal_separator == ',':
        # thousands = '.', decimal = ','
        formatted = f'{abs(v):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
    else:
        formatted = f'{abs(v):,.2f}'

    sign = '-' if v < 0 else ''
    sym = prefs.currency_symbol or ''
    if prefs.currency_before:
        return f'{sign}{sym}{formatted}'
    return f'{sign}{formatted} {sym}'


@register.filter
def num(value, prefs):
    """Format a number with correct decimal separator but no currency symbol.

    prefs may be a UserPreferences object or anything with .decimal_separator.
    If prefs also has .decimal_places (set by the view for account-specific
    formatting), that overrides the default of 2.

    Usage: {{ value|num:prefs }}  or  {{ value|num:account_prefs }}
    """
    if prefs is None or value is None:
        return value
    try:
        v = Decimal(str(value))
    except Exception:
        return value
    dp  = getattr(prefs, 'decimal_places', 2)
    sep = getattr(prefs, 'decimal_separator', '.')
    fmt = f'{abs(v):,.{dp}f}'
    if sep == ',':
        fmt = fmt.replace(',', 'X').replace('.', ',').replace('X', '.')
    sign = '-' if v < 0 else ''
    return f'{sign}{fmt}'


@register.simple_tag
def num_for_acct(value, base_prefs, account):
    """Format value using the account's own smallest_fraction for decimal places."""
    if value is None or account is None:
        return value or ''
    try:
        v = Decimal(str(value))
    except Exception:
        return value
    sf = account.smallest_fraction or 100
    dp = round(_math.log10(sf)) if sf > 1 else 0
    sep = getattr(base_prefs, 'decimal_separator', '.')
    fmt = f'{abs(v):,.{dp}f}'
    if sep == ',':
        fmt = fmt.replace(',', 'X').replace('.', ',').replace('X', '.')
    sign = '-' if v < 0 else ''
    return f'{sign}{fmt}'


@register.filter
def udate(value, prefs):
    """Format a date using the user's date_format preference.
    Usage: {{ value|udate:prefs }}
    """
    if prefs is None or value is None:
        return value
    try:
        return value.strftime(prefs.date_format)
    except Exception:
        return value
