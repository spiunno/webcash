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
    'gnc': 'http://www.gnucash.org/XML/gnc',
    'act': 'http://www.gnucash.org/XML/act',
    'trn': 'http://www.gnucash.org/XML/trn',
    'split': 'http://www.gnucash.org/XML/split',
    'ts': 'http://www.gnucash.org/XML/ts',
    'cmdty': 'http://www.gnucash.org/XML/cmdty',
}

GNUCASH_TYPE_MAP = {
    'ASSET': Account.ASSET,
    'BANK': Account.ASSET,
    'CASH': Account.ASSET,
    'CREDIT': Account.LIABILITY,
    'LIABILITY': Account.LIABILITY,
    'INCOME': Account.INCOME,
    'EXPENSE': Account.EXPENSE,
    'EQUITY': Account.EQUITY,
    'STOCK': Account.ASSET,
    'MUTUAL': Account.ASSET,
    'RECEIVABLE': Account.ASSET,
    'PAYABLE': Account.LIABILITY,
}


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


def import_file(path):
    """
    Import a GnuCash XML file. Returns a dict with counts of
    created/skipped accounts and transactions.
    """
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

    for act_el in book.findall('gnc:account', NS):
        act_guid = act_el.findtext('act:id', namespaces=NS)
        act_name = act_el.findtext('act:name', namespaces=NS) or ''
        act_type_raw = act_el.findtext('act:type', namespaces=NS) or 'ASSET'
        act_type = GNUCASH_TYPE_MAP.get(act_type_raw.upper(), Account.ASSET)
        placeholder = act_el.findtext('act:slots/slot/slot:value', namespaces={
            **NS, 'slot': 'http://www.gnucash.org/XML/slot'
        }) == 'true'

        commodity_el = act_el.find('act:commodity', NS)
        mnemonic = 'USD'
        if commodity_el is not None:
            mnemonic = commodity_el.findtext('cmdty:id', namespaces=NS) or 'USD'

        parent_guid = act_el.findtext('act:parent', namespaces=NS)
        parent_obj = guid_to_account.get(parent_guid)

        act_uuid = uuid.UUID(act_guid) if act_guid else uuid.uuid4()

        obj, created = Account.objects.get_or_create(
            guid=act_uuid,
            defaults=dict(
                name=act_name,
                account_type=act_type,
                commodity_mnemonic=mnemonic,
                parent=parent_obj,
                placeholder=placeholder,
            )
        )
        guid_to_account[act_guid] = obj
        if created:
            stats['accounts_created'] += 1
        else:
            stats['accounts_skipped'] += 1

    # --- Pass 2: transactions ---
    for trn_el in book.findall('gnc:transaction', NS):
        trn_guid = trn_el.findtext('trn:id', namespaces=NS)
        trn_uuid = uuid.UUID(trn_guid) if trn_guid else uuid.uuid4()

        if Transaction.objects.filter(guid=trn_uuid).exists():
            stats['transactions_skipped'] += 1
            continue

        date_str = trn_el.findtext('trn:date-posted/ts:date', namespaces=NS) or ''
        try:
            post_date = _parse_date(date_str)
        except ValueError:
            post_date = date.today()

        description = trn_el.findtext('trn:description', namespaces=NS) or ''

        trn_obj = Transaction.objects.create(
            guid=trn_uuid,
            post_date=post_date,
            description=description,
        )

        for split_el in trn_el.findall('trn:splits/trn:split', NS):
            split_guid = split_el.findtext('split:id', namespaces=NS)
            split_uuid = uuid.UUID(split_guid) if split_guid else uuid.uuid4()
            split_account_guid = split_el.findtext('split:account', namespaces=NS)
            split_account = guid_to_account.get(split_account_guid)
            if split_account is None:
                continue

            value_str = split_el.findtext('split:value', namespaces=NS) or '0/1'
            value = _parse_value(value_str)
            memo = split_el.findtext('split:memo', namespaces=NS) or ''
            reconcile_state = split_el.findtext('split:reconciled-state', namespaces=NS) or 'n'

            Split.objects.create(
                guid=split_uuid,
                transaction=trn_obj,
                account=split_account,
                value_num=value,
                memo=memo,
                reconciled=(reconcile_state == 'y'),
            )

        stats['transactions_created'] += 1

    return stats
