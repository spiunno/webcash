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
