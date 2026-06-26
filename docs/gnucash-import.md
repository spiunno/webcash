# GnuCash XML Import

## Supported file formats

| Format | Extension | Notes |
|---|---|---|
| GnuCash XML (plain) | `.gnucash`, `.xml` | Standard uncompressed export |
| GnuCash XML (compressed) | `.gnucash`, `.gz` | Default GnuCash save format — detected automatically by magic bytes |

The importer reads the file header to detect gzip compression and opens it
transparently; you do not need to decompress the file first.

## How to import

### Via the web UI

1. Sign in and go to **Import GnuCash file** in the sidebar (or `/import/`).
2. Choose your `.gnucash` file and click **Import**.
3. A summary message reports how many accounts and transactions were created or skipped.

### Via the command line

```bash
python manage.py shell -c "
from ledger.gnucash_import import import_file
stats = import_file('/path/to/your-file.gnucash')
print(stats)
"
```

`import_file` returns a dict:

```python
{
  'accounts_created': 42,
  'accounts_skipped': 0,
  'transactions_created': 1839,
  'transactions_skipped': 0,
}
```

## Idempotency

Every account and transaction is keyed on its **GnuCash GUID** (a UUID stored
in `act:id` / `trn:id`). Re-importing the same file will skip any record whose
GUID already exists in the database. You can safely import the same file
multiple times or import incremental exports — duplicates are never created.

## What gets imported

### Accounts

- Full account tree, preserving parent/child hierarchy
- Account type is mapped from GnuCash's extended set to WebCash's five types:

  | GnuCash type | WebCash type |
  |---|---|
  | ASSET, BANK, CASH, STOCK, MUTUAL, RECEIVABLE | ASSET |
  | LIABILITY, CREDIT, PAYABLE | LIABILITY |
  | INCOME | INCOME |
  | EXPENSE | EXPENSE |
  | EQUITY | EQUITY |

- Commodity mnemonic (e.g. `USD`, `EUR`) is preserved per account
- `placeholder` flag is imported

### Transactions

- Post date (the date the transaction occurred, not when it was entered)
- Description
- All splits with exact decimal values

### What is NOT imported

- Scheduled transactions
- Budget data
- Price history / currency exchange rates
- Account notes / slot data beyond `placeholder`
- Multi-currency split quantities (only the value in the transaction currency is stored)

## Value precision

GnuCash stores monetary values as integer fractions, e.g. `−500000/100`
meaning −5000.00. The importer converts these to Python `Decimal` before
writing to the database, which stores them as `decimal(18,4)`. No
floating-point arithmetic is involved at any stage.

## Parsing internals

The importer is in [`ledger/gnucash_import.py`](../ledger/gnucash_import.py).
It uses `xml.etree.ElementTree` from the Python standard library — no third-party
XML library is required.

The parsing is a two-pass process:

1. **Pass 1 — accounts**: iterate `gnc:account` elements in document order
   (GnuCash writes parents before children), resolve parent GUIDs to Django
   PKs, call `Account.objects.get_or_create(guid=...)`.

2. **Pass 2 — transactions**: iterate `gnc:transaction` elements, skip any
   whose GUID already exists, create `Transaction` and all child `Split`
   records in one Django ORM call each.
