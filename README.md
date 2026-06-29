# WebCash

A self-hosted family finance ledger inspired by GnuCash, built with Django. Supports SQLite (default) and PostgreSQL.

- Double-entry bookkeeping with accounts, transactions, and splits
- GnuCash XML file import (plain or gzip-compressed)
- Account tree sidebar (Assets, Liabilities, Income, Expenses, Equity)
- Transaction register with running balance per account
- Net worth and income vs. expense summary dashboard
- Shared ledger for two users (husband & wife)

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser --username husband
python manage.py runserver
```

Open http://127.0.0.1:8000 and sign in.

## Documentation

| | |
|---|---|
| [docs/architecture.md](docs/architecture.md) | System design, data model, request flow |
| [docs/install.md](docs/install.md) | Prerequisites and first-time setup |
| [docs/running.md](docs/running.md) | Starting the server, day-to-day commands |
| [docs/gnucash-import.md](docs/gnucash-import.md) | How the GnuCash XML importer works |
