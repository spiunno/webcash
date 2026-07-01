"""
Parse an American Express Italy ("Blu American Express" / Carta Platinum/Gold
Credit) monthly statement PDF and load its transactions into a single
Credit Card account.

The statement lists, in euro, every purchase/fee posted in the period plus
one "ADDEBITO IN C/C SALVO BUON FINE" line: the direct-debit payment taken
from the linked checking account to settle the previous statement. Despite
its name (which refers to the debit on the *bank* account), from the card
account's point of view this is a credit that reduces the balance owed, so
it's parsed with the sign flipped relative to ordinary purchases/fees.

Rows have no stable identifier of their own, so a guid is derived from the
row's fields — re-importing the same statement is therefore a no-op.
"""
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

import pdfplumber

from .fineco_import import _get_or_create_imbalance_account, _suggest_counterpart_account
from .models import Split, Transaction

AMEX_NAMESPACE = uuid.NAMESPACE_URL

CARD_NUMBER_PATTERN = re.compile(r"\b(?P<carta>[xX]{4}-[xX]{6}-\d+)\b")
TRANSACTIONS_HEADER = re.compile(r"^Data\s+Contabilizzata in\s+Descrizione dell'operazione")
INTERESSI_HEADER = "INTERESSI, ALTRI ADDEBITI E ACCREDITI"
SECTION_END_MARKERS = ("Cashback",)

TRANSACTION_PATTERN = re.compile(
    r"^(?P<data_operazione>\d{2}\.\d{2}\.\d{2})\s+"
    r"(?P<data_registrazione>\d{2}\.\d{2}\.\d{2})\s+"
    r"(?P<descrizione>.+?)"
    r"(?:\s+\d[\d,]*\.\d{2})?"  # optional foreign-currency amount (e.g. "58.41")
    r"\s+(?P<importo>-?\d[\d.]*,\d{2})$"
)
TOTAL_SPESE_PATTERN = re.compile(r"^Totale nuove spese.*?(?P<importo>-?\d[\d.]*,\d{2})$")
TOTAL_INTERESSI_PATTERN = re.compile(r"^Totale interessi.*?(?P<importo>-?\d[\d.]*,\d{2})$")

PAYMENT_MARKER = "ADDEBITO IN C/C"


def extract_rows(pdf_path):
    """
    Extract raw transaction rows (Italian-formatted dates/amounts) plus the
    statement's own section totals (for sanity-checking) and the masked card
    number, from the "Operazioni contabilizzate" section of the PDF.
    """
    rows = []
    totals = []  # list of (section, Decimal) tuples
    in_section = False
    section = "spese"
    card_number = ""

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""

            for raw_line in text.split("\n"):
                line = raw_line.strip()
                if not line:
                    continue

                if not card_number:
                    m = CARD_NUMBER_PATTERN.search(line)
                    if m:
                        card_number = m.group("carta")

                if not in_section:
                    if TRANSACTIONS_HEADER.match(line):
                        in_section = True
                    continue

                if TRANSACTIONS_HEADER.match(line) or line == "operazione data":
                    continue
                if any(line.startswith(marker) for marker in SECTION_END_MARKERS):
                    return rows, totals, card_number
                if line == INTERESSI_HEADER:
                    section = "interessi"
                    continue

                total_match = TOTAL_SPESE_PATTERN.match(line) or TOTAL_INTERESSI_PATTERN.match(line)
                if total_match:
                    totals.append((section, _parse_amount(total_match.group("importo"))))
                    if section == "interessi":
                        return rows, totals, card_number
                    continue

                m = TRANSACTION_PATTERN.match(line)
                if m:
                    descrizione = m.group("descrizione").strip()
                    importo = _parse_amount(m.group("importo"))
                    is_payment = PAYMENT_MARKER in descrizione.upper()
                    if is_payment:
                        importo = -importo
                    rows.append({
                        "data_operazione": m.group("data_operazione"),
                        "data_registrazione": m.group("data_registrazione"),
                        "descrizione": descrizione,
                        "importo": importo,
                        "importo_str": m.group("importo"),
                        "section": section,
                        "is_payment": is_payment,
                    })

    return rows, totals, card_number


def _validate_totals(rows, totals):
    """Compare the PDF's own per-section totals against the extracted rows."""
    warnings = []
    for section, expected in totals:
        actual = sum(
            (r["importo"] for r in rows if r["section"] == section and not r["is_payment"]),
            Decimal("0"),
        )
        if actual != expected:
            warnings.append(
                f"{section}: statement total {expected:.2f} but extracted rows sum to {actual:.2f}"
            )
    return warnings


def _parse_short_date(short_date):
    """Convert 'DD.MM.YY' to a date object."""
    day, month, year = short_date.split(".")
    year_full = int(year) + (2000 if int(year) < 50 else 1900)
    return date(year_full, int(month), int(day))


def _parse_amount(amount_str):
    """Convert Italian-format amount (e.g. '1.234,56') to Decimal, or None if blank."""
    if not amount_str or not amount_str.strip():
        return None
    try:
        return Decimal(amount_str.replace(".", "").replace(",", "."))
    except InvalidOperation:
        return None


def _import_rows(rows, account, card_number, stats, _cb):
    currency = account.commodity_mnemonic
    _cb('Importing card transactions', 0, len(rows))
    seen_counts = {}

    for i, row in enumerate(rows):
        amount = row['importo']
        description = row['descrizione']
        post_date = _parse_short_date(row['data_operazione'])

        # Rows have no id of their own; an occurrence index disambiguates
        # genuinely repeated rows (e.g. two identical same-day charges) from
        # a rescan of the same statement, which reproduces the same sequence.
        key = (row['data_operazione'], row['data_registrazione'], row['importo_str'], description)
        occurrence = seen_counts.get(key, 0)
        seen_counts[key] = occurrence + 1

        guid = uuid.uuid5(
            AMEX_NAMESPACE,
            f"amex:{account.pk}:{row['data_operazione']}:{row['data_registrazione']}:"
            f"{row['importo_str']}:{description}:{occurrence}",
        )

        if Transaction.objects.filter(guid=guid).exists():
            stats['transactions_skipped'] += 1
            _cb('Importing card transactions', i + 1, len(rows))
            continue

        counterpart = _suggest_counterpart_account(account, description)
        if counterpart is not None:
            stats['categorized'] += 1
        else:
            counterpart = _get_or_create_imbalance_account(currency)
            stats['uncategorized'] += 1

        txn = Transaction.objects.create(
            guid=guid, post_date=post_date, description=description, currency=currency,
        )
        # A positive importo is a purchase/fee (increases what's owed); a
        # negative one (the ADDEBITO IN C/C payment row) is a credit. The
        # card account is a liability, so a purchase is a negative split
        # there (see Account.normal_balance).
        Split.objects.create(transaction=txn, account=account, value_num=-amount, memo=card_number)
        Split.objects.create(transaction=txn, account=counterpart, value_num=amount)
        stats['transactions_created'] += 1

        _cb('Importing card transactions', i + 1, len(rows))


def import_file(pdf_path, account, progress_cb=None):
    """
    Import an American Express statement PDF into `account` (a Credit Card
    account). Returns a dict with counts of created/skipped transactions,
    how many were auto-categorized, and any statement-total mismatches.

    progress_cb(phase, current, total) is called periodically when provided.
    """
    def _cb(phase, current, total):
        if progress_cb:
            progress_cb(phase, current, total)

    _cb('Reading PDF', 0, 1)
    rows, totals, card_number = extract_rows(pdf_path)
    _cb('Reading PDF', 1, 1)

    stats = {
        'transactions_created': 0, 'transactions_skipped': 0,
        'categorized': 0, 'uncategorized': 0,
        'total_warnings': [],
    }

    stats['total_warnings'] = _validate_totals(rows, totals)
    _import_rows(rows, account, card_number, stats, _cb)

    return stats
