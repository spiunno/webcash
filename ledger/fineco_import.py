"""
Parse a Fineco quarterly bank statement PDF and load its transactions into
the database. The PDF has two sections handled here:

  - "Movimenti" (conto corrente): posted against a user-chosen Bank account.
  - "Carte di credito e ricaricabili": posted against a user-chosen Credit
    Card account.

Each PDF row becomes a Transaction with two splits: one on the target
account, and a counterpart guessed from a past transaction with the same
description on that account (falling back to an 'Imbalance-<currency>'
account when nothing matches, so it's easy to find and recategorize later).

Rows have no stable identifier of their own, so a guid is derived from the
row's fields — re-importing the same statement is therefore a no-op.
"""
import re
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation

import pdfplumber

from .models import Account, Split, Transaction

FINECO_NAMESPACE = uuid.NAMESPACE_URL

DATE_PATTERN = re.compile(r"^(\d{2}\.\d{2}\.\d{2})\s+(\d{2}\.\d{2}\.\d{2})\s+")
AMOUNT_AT_START = re.compile(r"^(\d[\d.]*,\d{2})(?:\s+(\d[\d.]*,\d{2}))?\s+(.*)")
AMOUNT_ONLY = re.compile(r"^(\d[\d.]*,\d{2})\s+(.*)")
DATE_TOKEN = re.compile(r"^\d{2}\.\d{2}\.\d{2}$")
AMOUNT_TOKEN = re.compile(r"^\d[\d.]*,\d{2}$")

SECTION_END_MARKERS = (
    "Saldo finale in euro",
    "Calcolo per liquidazione",
    "segue a pagina",
    "FinecoBank S.p.A.",
)
NOISE_MARKERS = (
    "FinecoBank S.p.A",
    "Reggio Emilia",
    "Cap.Soc.",
    "marchio concesso",
    "Codice Fiscale",
    "L000",  # barcode artifacts
)

CARD_SECTION_MARKER = "Carte di credito e ricaricabili"
CARD_SECTION_END_MARKERS = (
    "Deposito titoli",
    "Imposta di bollo assolta",
    "TUTELA DEL SISTEMA",
)
CARD_TRANSACTION_PATTERN = re.compile(
    r"^(?P<data_operazione>\d{2}\.\d{2}\.\d{2})\s+"
    r"(?P<data_registrazione>\d{2}\.\d{2}\.\d{2})\s+"
    r"(?P<descrizione>.+?)\s+"
    r"(?P<importo>-?\d[\d.]*,\d{2})$"
)
CARD_MONTH_PATTERN = re.compile(r"Movimenti\s+(?P<mese>[A-Za-zÀ-ÖØ-öø-ÿ]+)")
CARD_TOTAL_PATTERN = re.compile(r"^Totale\s+(?P<importo>-?\d[\d.]*,\d{2})$")
CARD_HEADER_PATTERN = re.compile(
    r"Carta\s+N\.\s*:\s*(?P<carta>.+?)\s+"
    r"Titolare\s*:\s*(?P<titolare>.+?)"
    r"(?:\s+Plafond\s*:\s*(?P<plafond>[\d.]+))?$"
)


def _extract_movimenti_lines(pdf):
    """Collect all text lines from the Movimenti section across pages."""
    lines = []
    in_movimenti = False

    for page in pdf.pages:
        text = page.extract_text()
        if not text:
            continue

        for line in text.split("\n"):
            if "Movimenti" in line and "Numero Conto" in line:
                in_movimenti = True
                continue
            if not in_movimenti and re.match(r"^DATA\s+DATA\s+USCITE\s+ENTRATE", line):
                in_movimenti = True
                continue

            if in_movimenti:
                if any(marker in line for marker in SECTION_END_MARKERS):
                    if "Saldo finale" in line:
                        in_movimenti = False
                    continue
                if re.match(r"^DATA\s+DATA\s+USCITE", line):
                    continue
                if re.match(r"^OPERAZIONE\s+VALUTA", line):
                    continue
                if "Saldo iniziale in euro" in line:
                    continue
                if "Estratto conto" in line:
                    continue

                stripped = line.strip()
                if not stripped or stripped.startswith("(cid:"):
                    continue
                if re.match(r"^PAGINA \d+", stripped):
                    continue

                lines.append(stripped)

    return lines


def _parse_movimenti_lines(lines):
    """Turn collected lines into row dicts, folding wrapped description lines in."""
    rows = []

    for line in lines:
        m = DATE_PATTERN.match(line)
        if m:
            rest = line[m.end():].strip()
            uscite = entrate = ""
            descrizione = rest

            amt_match = AMOUNT_AT_START.match(rest)
            if amt_match:
                amt1, amt2, desc = amt_match.groups()
                if amt2:
                    uscite, entrate, descrizione = amt1, amt2, desc
                else:
                    uscite, descrizione = amt1, desc
            else:
                entrate_match = AMOUNT_ONLY.match(rest)
                if entrate_match:
                    entrate, descrizione = entrate_match.groups()
                else:
                    descrizione = rest

            rows.append({
                "data_operazione": m.group(1),
                "data_valuta": m.group(2),
                "uscite": uscite,
                "entrate": entrate,
                "descrizione": descrizione,
            })
        elif rows:
            if any(noise in line for noise in NOISE_MARKERS):
                continue
            if re.match(r"^[\d\-/]+$", line) and len(line) < 30:
                continue
            rows[-1]["descrizione"] += " " + line

    return rows


def _fix_amounts_with_positions(pdf, rows):
    """Use word x-coordinates to correctly assign single amounts to uscite/entrate."""
    entrate_x = None
    for page in pdf.pages:
        for w in page.extract_words():
            if w["text"] == "ENTRATE":
                entrate_x = float(w["x0"])
                break
        if entrate_x is not None:
            break
    if entrate_x is None:
        return  # Can't fix without column positions

    entrate_threshold = entrate_x - 10
    row_index = 0

    for page in pdf.pages:
        lines_by_y = {}
        for w in page.extract_words():
            y_key = round(float(w["top"]) / 3)
            lines_by_y.setdefault(y_key, []).append(w)

        for y_key in sorted(lines_by_y.keys()):
            line_words = sorted(lines_by_y[y_key], key=lambda w: float(w["x0"]))
            if len(line_words) < 3:
                continue
            if not (DATE_TOKEN.match(line_words[0]["text"]) and DATE_TOKEN.match(line_words[1]["text"])):
                continue
            if row_index >= len(rows):
                break

            amounts_on_line = [w for w in line_words[2:] if AMOUNT_TOKEN.match(w["text"])]
            if len(amounts_on_line) == 1:
                amt_x = float(amounts_on_line[0]["x0"])
                if amt_x >= entrate_threshold:
                    rows[row_index]["entrate"] = amounts_on_line[0]["text"]
                    rows[row_index]["uscite"] = ""
                else:
                    rows[row_index]["uscite"] = amounts_on_line[0]["text"]
                    rows[row_index]["entrate"] = ""
            elif len(amounts_on_line) >= 2:
                rows[row_index]["uscite"] = amounts_on_line[0]["text"]
                rows[row_index]["entrate"] = amounts_on_line[1]["text"]

            row_index += 1


def extract_rows(pdf_path):
    """Extract raw transaction rows (Italian-formatted dates/amounts) from the PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        lines = _extract_movimenti_lines(pdf)
        rows = _parse_movimenti_lines(lines)
        _fix_amounts_with_positions(pdf, rows)
    return rows


def _clean_card_line(line):
    return re.sub(r"\s+", " ", line.replace("\xa0", " ")).strip()


def _is_card_noise_line(line):
    if not line:
        return True
    if line in {
        "DATA DATA DESCRIZIONE IMPORTO",
        "OPERAZIONE REGISTRAZIONE OPERAZIONE IN EURO",
    }:
        return True
    if line.startswith(("Estratto conto", "PAGINA ", "segue a pagina")):
        return True
    if line.startswith(("FinecoBank S.p.A.", "interamente sottoscritto", "Codice Fiscale", "PEC:")):
        return True
    if re.match(r"^\d{2}/\d+-", line):
        return True
    if re.fullmatch(r"\d+", line):
        return True
    return False


def _parse_card_header(line):
    m = CARD_HEADER_PATTERN.search(line)
    if not m:
        return "", ""
    return m.group("carta").strip(), m.group("titolare").strip()


def extract_card_rows(pdf_path):
    """
    Extract rows from the "Carte di credito e ricaricabili" section, plus the
    PDF's own monthly/per-card totals (used to sanity-check the extraction).
    """
    rows = []
    totals = []
    in_section = False
    current_month = ""
    current_card = ""
    current_holder = ""
    last_row = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""

            for raw_line in text.split("\n"):
                line = _clean_card_line(raw_line)

                if not in_section:
                    if CARD_SECTION_MARKER in line:
                        in_section = True
                    continue

                if any(line.startswith(marker) for marker in CARD_SECTION_END_MARKERS):
                    return rows, totals

                if _is_card_noise_line(line):
                    continue

                month_match = CARD_MONTH_PATTERN.search(line)
                if month_match:
                    current_month = month_match.group("mese")
                    last_row = None
                    continue

                if "Carta N." in line:
                    current_card, current_holder = _parse_card_header(line)
                    last_row = None
                    continue

                total_match = CARD_TOTAL_PATTERN.match(line)
                if total_match:
                    totals.append({
                        "mensilita": current_month,
                        "carta": current_card,
                        "importo": total_match.group("importo"),
                    })
                    last_row = None
                    continue

                m = CARD_TRANSACTION_PATTERN.match(line)
                if m:
                    row = {
                        "mensilita": current_month,
                        "carta": current_card,
                        "titolare": current_holder,
                        "data_operazione": m.group("data_operazione"),
                        "data_registrazione": m.group("data_registrazione"),
                        "descrizione": m.group("descrizione").strip(),
                        "importo": m.group("importo"),
                    }
                    rows.append(row)
                    last_row = row
                    continue

                # Rare wrapped descriptions: append only while inside a month/card block.
                if last_row is not None and current_month and current_card:
                    last_row["descrizione"] += f" {line}"

    return rows, totals


def _validate_card_totals(rows, totals):
    """Compare the PDF's own per-month/per-card totals against the extracted rows."""
    warnings = []
    for total in totals:
        expected = _parse_amount(total["importo"])
        actual = sum(
            (_parse_amount(r["importo"]) for r in rows
             if r["mensilita"] == total["mensilita"] and r["carta"] == total["carta"]),
            Decimal("0"),
        )
        if actual != expected:
            warnings.append(
                f"{total['mensilita']} {total['carta']}: statement total {total['importo']} "
                f"but extracted rows sum to {actual:.2f}"
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


def _suggest_counterpart_account(account, description):
    """Reuse the counterpart account from a past transaction with the same description."""
    if not description:
        return None
    candidates = (
        Split.objects
        .filter(account=account, transaction__description__iexact=description)
        .prefetch_related('transaction__splits__account')
        .order_by('-transaction__post_date')
    )
    for split in candidates:
        others = [s for s in split.transaction.splits.all() if s.account_id != account.pk]
        if len(others) == 1:
            return others[0].account
    return None


def _get_or_create_imbalance_account(currency):
    account, _ = Account.objects.get_or_create(
        name=f'Imbalance-{currency}',
        parent=None,
        defaults=dict(account_type=Account.EXPENSE, commodity_mnemonic=currency),
    )
    return account


def _import_checking_rows(rows, account, stats, _cb):
    currency = account.commodity_mnemonic
    _cb('Importing checking transactions', 0, len(rows))
    seen_counts = {}

    for i, row in enumerate(rows):
        entrate_amt = _parse_amount(row['entrate'])
        uscite_amt = _parse_amount(row['uscite'])
        amount = entrate_amt if entrate_amt is not None else -(uscite_amt or Decimal('0'))

        description = row['descrizione'].strip()
        post_date = _parse_short_date(row['data_operazione'])

        # Rows have no id of their own; an occurrence index disambiguates
        # genuinely repeated rows (e.g. two identical same-day charges) from
        # a rescan of the same statement, which reproduces the same sequence.
        key = (row['data_operazione'], row['data_valuta'], row['uscite'], row['entrate'], description)
        occurrence = seen_counts.get(key, 0)
        seen_counts[key] = occurrence + 1

        guid = uuid.uuid5(
            FINECO_NAMESPACE,
            f"fineco:{account.pk}:{row['data_operazione']}:{row['data_valuta']}:"
            f"{row['uscite']}:{row['entrate']}:{description}:{occurrence}",
        )

        if Transaction.objects.filter(guid=guid).exists():
            stats['transactions_skipped'] += 1
            _cb('Importing checking transactions', i + 1, len(rows))
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
        Split.objects.create(transaction=txn, account=account, value_num=amount)
        Split.objects.create(transaction=txn, account=counterpart, value_num=-amount)
        stats['transactions_created'] += 1

        _cb('Importing checking transactions', i + 1, len(rows))


def _import_card_rows(rows, account, stats, _cb):
    currency = account.commodity_mnemonic
    _cb('Importing card transactions', 0, len(rows))
    seen_counts = {}

    for i, row in enumerate(rows):
        # A positive importo is a purchase (increases what's owed); a negative
        # one is a refund/credit. The card account is a liability, so a
        # purchase is recorded as a negative split there (see Account.normal_balance).
        amount = _parse_amount(row['importo']) or Decimal('0')
        description = row['descrizione'].strip()
        post_date = _parse_short_date(row['data_operazione'])

        # See _import_checking_rows: an occurrence index disambiguates
        # genuinely repeated rows from a rescan of the same statement.
        key = (row['carta'], row['data_operazione'], row['data_registrazione'], row['importo'], description)
        occurrence = seen_counts.get(key, 0)
        seen_counts[key] = occurrence + 1

        guid = uuid.uuid5(
            FINECO_NAMESPACE,
            f"fineco-carta:{account.pk}:{row['carta']}:{row['data_operazione']}:"
            f"{row['data_registrazione']}:{row['importo']}:{description}:{occurrence}",
        )

        if Transaction.objects.filter(guid=guid).exists():
            stats['card_transactions_skipped'] += 1
            _cb('Importing card transactions', i + 1, len(rows))
            continue

        counterpart = _suggest_counterpart_account(account, description)
        if counterpart is not None:
            stats['card_categorized'] += 1
        else:
            counterpart = _get_or_create_imbalance_account(currency)
            stats['card_uncategorized'] += 1

        txn = Transaction.objects.create(
            guid=guid, post_date=post_date, description=description, currency=currency,
        )
        Split.objects.create(transaction=txn, account=account, value_num=-amount, memo=row['carta'])
        Split.objects.create(transaction=txn, account=counterpart, value_num=amount)
        stats['card_transactions_created'] += 1

        _cb('Importing card transactions', i + 1, len(rows))


def import_file(pdf_path, account, card_account=None, progress_cb=None):
    """
    Import a Fineco statement PDF: checking ("Movimenti") rows go to `account`,
    and — when `card_account` is given — credit card rows go to `card_account`.
    Returns a dict with counts of created/skipped transactions and how many
    were auto-categorized, for each of the two sections.

    progress_cb(phase, current, total) is called periodically when provided.
    """
    def _cb(phase, current, total):
        if progress_cb:
            progress_cb(phase, current, total)

    _cb('Reading PDF', 0, 1)
    rows = extract_rows(pdf_path)
    card_rows, card_totals = extract_card_rows(pdf_path) if card_account else ([], [])
    _cb('Reading PDF', 1, 1)

    stats = {
        'transactions_created': 0, 'transactions_skipped': 0,
        'categorized': 0, 'uncategorized': 0,
        'card_transactions_created': 0, 'card_transactions_skipped': 0,
        'card_categorized': 0, 'card_uncategorized': 0,
        'card_total_warnings': [],
    }

    _import_checking_rows(rows, account, stats, _cb)

    if card_account:
        stats['card_total_warnings'] = _validate_card_totals(card_rows, card_totals)
        _import_card_rows(card_rows, card_account, stats, _cb)

    return stats
