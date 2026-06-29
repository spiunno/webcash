# Installation

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.9 or later | `python3 --version` |
| pip | any recent | comes with Python |
| Git | any | to clone the repo |
| PostgreSQL | 12 or later | optional — SQLite is used by default |

No Node.js, no Docker required.

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
pip install -r requirements.txt
```

This installs Django and `psycopg2-binary` (the PostgreSQL driver, harmless if you stay on SQLite).

## 4. Choose a database

### Option A — SQLite (default, zero config)

Nothing to do. Skip to step 5.

### Option B — PostgreSQL

**4a.** Create the database:

```bash
createdb webcash
```

**4b.** Export the connection URL before running any `manage.py` command:

```bash
export DATABASE_URL=postgres://your_user:your_password@localhost:5432/webcash
```

If your PostgreSQL uses peer/trust auth (no password needed), omit the password:

```bash
export DATABASE_URL=postgres://your_user@localhost:5432/webcash
```

Add this `export` to your shell profile (`.zshrc`, `.bashrc`, etc.) or to a `.env` file you source before running the app, so you don't have to repeat it every session.

## 5. Apply database migrations

```bash
python manage.py migrate
```

With SQLite this creates `db.sqlite3` in the project root. With PostgreSQL it populates the database you created above.

## 6. Create user accounts

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

## 7. (Optional) Load sample data

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
