# Installation

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.9 or later | `python3 --version` |
| pip | any recent | comes with Python |
| Git | any | to clone the repo |

No database server, no Node.js, no Docker required.

## 1. Clone the repository

```bash
git clone <repo-url> webcash
cd webcash
```

## 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
```

## 3. Install dependencies

```bash
pip install django==4.2
```

There are no other runtime dependencies — the GnuCash importer uses Python's stdlib `xml.etree.ElementTree` and `gzip`.

To pin the exact version for reproducibility:

```bash
pip freeze > requirements.txt   # save
pip install -r requirements.txt # restore
```

## 4. Apply database migrations

```bash
python manage.py migrate
```

This creates `db.sqlite3` in the project root with all tables.

## 5. Create user accounts

WebCash ships without any users. Create the two household accounts:

```bash
python manage.py createsuperuser --username husband
python manage.py createsuperuser --username wife
```

Or do it non-interactively in a shell:

```bash
python manage.py shell -c "
from django.contrib.auth.models import User
User.objects.create_user('husband', password='choose-a-strong-password')
User.objects.create_user('wife',    password='choose-a-strong-password')
"
```

Both users share the same ledger. There is no data separation between them.

## 6. (Optional) Load sample data

If you have a GnuCash file, you can import it from the command line before
starting the server:

```bash
python manage.py shell -c "
from ledger.gnucash_import import import_file
stats = import_file('path/to/your-file.gnucash')
print(stats)
"
```

Or use the web UI at `/import/` after the server is running.

## Directory layout after install

```
webcash/
├── db.sqlite3          ← SQLite database (git-ignored)
├── manage.py
├── webcash/            ← project settings / root URLs
├── ledger/             ← app: models, views, importer
├── templates/          ← HTML templates
└── docs/               ← this documentation
```
