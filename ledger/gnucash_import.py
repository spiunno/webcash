"""
Parse a GnuCash XML file and load accounts + transactions into the database.

GnuCash XML uses the GnuCash namespace (gnc:) and stores values as
numerator/denominator pairs, e.g. <split:value>-5000/100</split:value>.
"""
import gzip
import uuid
from decimal import Decimal
from datetime import date
from xml.etree import ElementTree as ET

from .models import Account, Transaction, Split

NS = {
    'gnc':   'http://www.gnucash.org/XML/gnc',
    'act':   'http://www.gnucash.org/XML/act',
    'trn':   'http://www.gnucash.org/XML/trn',
    'split': 'http://www.gnucash.org/XML/split',
    'ts':    'http://www.gnucash.org/XML/ts',
    'cmdty': 'http://www.gnucash.org/XML/cmdty',
    'slot':  'http://www.gnucash.org/XML/slot',
}

# Maps GnuCash account types to our model constants (preserving all subtypes)
GNUCASH_TYPE_MAP = {
    'ASSET':      Account.ASSET,
    'BANK':       Account.BANK,
    'CASH':       Account.CASH,
    'CREDIT':     Account.CREDIT,
    'CREDIT CARD': Account.CREDIT,
    'LIABILITY':  Account.LIABILITY,
    'INCOME':     Account.INCOME,
    'EXPENSE':    Account.EXPENSE,
    'EQUITY':     Account.EQUITY,
    'STOCK':      Account.STOCK,
    'MUTUAL':     Account.MUTUAL,
    'RECEIVABLE': Account.RECEIVABLE,
    'PAYABLE':    Account.PAYABLE,
    'ROOT':       Account.EQUITY,  # invisible root
}

# Account types that hold securities rather than currency units
SECURITY_TYPES = {'STOCK', 'MUTUAL'}


def _parse_value(fraction_str):
    """Convert GnuCash '1234/100' fraction string to Decimal."""
    if '/' in fraction_str:
        num, den = fraction_str.split('/', 1)
        return Decimal(num) / Decimal(den)
    return Decimal(fraction_str)


def _parse_date(date_str):
    """Parse '2024-01-15 00:00:00 +0000' → date(2024, 1, 15)."""
    return date.fromisoformat(date_str.strip().split(' ')[0])


def _open_file(path):
    """Open plain or gzip-compressed GnuCash file."""
    with open(path, 'rb') as f:
        magic = f.read(2)
    if magic == b'\x1f\x8b':
        return gzip.open(path, 'rb')
    return open(path, 'rb')


def import_file(path, progress_cb=None):
    """
    Import a GnuCash XML file. Returns a dict with counts of
    created/skipped accounts and transactions.

    progress_cb(phase, current, total) is called periodically when provided.
    """
    def _cb(phase, current, total):
        if progress_cb:
            progress_cb(phase, current, total)

    with _open_file(path) as f:
        tree = ET.parse(f)

    root = tree.getroot()
    book = root.find('gnc:book', NS)
    if book is None:
        raise ValueError('No gnc:book element found — not a valid GnuCash XML file.')

    stats = {'accounts_created': 0, 'accounts_skipped': 0,
             'transactions_created': 0, 'transactions_skipped': 0}

    # --- Pass 1: accounts (tree order, parents before children) ---
    guid_to_account = {}
    act_els = book.findall('gnc:account', NS)
    _cb('Importing accounts', 0, len(act_els))

    for i, act_el in enumerate(act_els):
        act_guid = act_el.findtext('act:id', namespaces=NS)
        act_name = act_el.findtext('act:name', namespaces=NS) or ''
        act_type_raw = act_el.findtext('act:type', namespaces=NS) or 'ASSET'
        act_type = GNUCASH_TYPE_MAP.get(act_type_raw.upper(), Account.ASSET)

        # Detect placeholder from the slots (key="placeholder", value="true")
        placeholder = False
        for slot_el in act_el.findall('act:slots/slot:slot', NS):
            if (slot_el.findtext('slot:key', namespaces=NS) == 'placeholder'
                    and slot_el.findtext('slot:value', namespaces=NS) == 'true'):
                placeholder = True
                break

        commodity_el = act_el.find('act:commodity', NS)
        mnemonic = 'EUR'
        namespace = Account.NAMESPACE_CURRENCY
        if commodity_el is not None:
            mnemonic = commodity_el.findtext('cmdty:id', namespaces=NS) or 'EUR'
            ns_raw = (commodity_el.findtext('cmdty:space', namespaces=NS) or '').upper()
            if ns_raw in ('NYSE', 'NASDAQ', 'EUREX', 'LSE') or act_type_raw.upper() in SECURITY_TYPES:
                namespace = Account.NAMESPACE_SECURITY

        description = act_el.findtext('act:description', namespaces=NS) or ''
        parent_guid = act_el.findtext('act:parent', namespaces=NS)
        parent_obj = guid_to_account.get(parent_guid)

        act_uuid = uuid.UUID(act_guid) if act_guid else uuid.uuid4()

        fields = dict(
            name=act_name,
            account_type=act_type,
            commodity_namespace=namespace,
            commodity_mnemonic=mnemonic,
            description=description,
            parent=parent_obj,
            placeholder=placeholder,
        )
        obj, created = Account.objects.update_or_create(guid=act_uuid, defaults=fields)
        guid_to_account[act_guid] = obj
        if created:
            stats['accounts_created'] += 1
        else:
            stats['accounts_skipped'] += 1
        _cb('Importing accounts', i + 1, len(act_els))

    # --- Pass 2: transactions ---
    trn_els = book.findall('gnc:transaction', NS)
    _cb('Importing transactions', 0, len(trn_els))

    for i, trn_el in enumerate(trn_els):
        trn_guid = trn_el.findtext('trn:id', namespaces=NS)
        trn_uuid = uuid.UUID(trn_guid) if trn_guid else uuid.uuid4()

        date_str = trn_el.findtext('trn:date-posted/ts:date', namespaces=NS) or ''
        try:
            post_date = _parse_date(date_str)
        except ValueError:
            post_date = date.today()

        description = trn_el.findtext('trn:description', namespaces=NS) or ''
        currency_el = trn_el.find('trn:currency', NS)
        trn_currency = 'EUR'
        if currency_el is not None:
            trn_currency = currency_el.findtext('cmdty:id', namespaces=NS) or 'EUR'

        trn_obj, trn_created = Transaction.objects.update_or_create(
            guid=trn_uuid,
            defaults=dict(post_date=post_date, description=description, currency=trn_currency),
        )
        if not trn_created:
            # Re-import: wipe old splits so they'll be recreated from the file
            trn_obj.splits.all().delete()
            stats['transactions_skipped'] += 1
        else:
            stats['transactions_created'] += 1
        _cb('Importing transactions', i + 1, len(trn_els))

        for split_el in trn_el.findall('trn:splits/trn:split', NS):
            split_guid = split_el.findtext('split:id', namespaces=NS)
            split_uuid = uuid.UUID(split_guid) if split_guid else uuid.uuid4()
            split_account_guid = split_el.findtext('split:account', namespaces=NS)
            split_account = guid_to_account.get(split_account_guid)
            if split_account is None:
                continue

            # value = amount in transaction's base currency (sums to 0)
            value_str = split_el.findtext('split:value', namespaces=NS) or '0/1'
            value = _parse_value(value_str)

            # quantity = amount in the account's own commodity (may differ for cross-currency/securities)
            qty_str = split_el.findtext('split:quantity', namespaces=NS) or '0/1'
            quantity = _parse_value(qty_str)
            # Only store quantity_num when it differs from value (avoids storing redundant data)
            quantity_num = quantity if quantity != value else None

            memo = split_el.findtext('split:memo', namespaces=NS) or ''
            reconcile_state = split_el.findtext('split:reconciled-state', namespaces=NS) or 'n'

            Split.objects.update_or_create(
                guid=split_uuid,
                defaults=dict(
                    transaction=trn_obj,
                    account=split_account,
                    value_num=value,
                    quantity_num=quantity_num,
                    memo=memo,
                    reconciled=(reconcile_state == 'y'),
                ),
            )

    return stats
