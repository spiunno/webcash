# Architecture

## Overview

WebCash is a standard Django monolith. There is one Django project (`webcash/`) and one app (`ledger/`). The database is SQLite, stored in `db.sqlite3` at the repository root. No external services are required.

```
Browser
  │  HTTP
  ▼
Django (manage.py runserver / gunicorn)
  │
  ├── webcash/urls.py          root URL dispatcher
  ├── ledger/urls.py           app-level URL patterns
  ├── ledger/views.py          request handlers
  ├── ledger/models.py         ORM models → SQLite
  ├── ledger/gnucash_import.py XML parser (stdlib only)
  └── templates/ledger/        Jinja-style Django templates
```

## Data model

### Double-entry bookkeeping

Every financial event is a **Transaction** with two or more **Splits**. The splits across all accounts in a transaction must sum to zero — debits equal credits. WebCash stores the raw signed value (positive = debit, negative = credit) and interprets sign based on the account's *normal balance direction*.

| Account type | Normal balance | Increases with |
|---|---|---|
| ASSET | Debit (+) | positive split value |
| EXPENSE | Debit (+) | positive split value |
| LIABILITY | Credit (−) | negative split value |
| INCOME | Credit (−) | negative split value |
| EQUITY | Credit (−) | negative split value |

### Schema

```
Account
  id          integer PK
  guid        UUID (unique) — matches GnuCash GUIDs for idempotent import
  name        varchar(255)
  account_type  enum: ASSET | LIABILITY | INCOME | EXPENSE | EQUITY
  commodity_mnemonic  varchar(10)  e.g. "USD", "EUR"
  parent_id   FK → Account (nullable, NULL = root)
  description text
  placeholder bool  — true means structural node, no direct postings
  hidden      bool

Transaction
  id          integer PK
  guid        UUID (unique)
  post_date   date
  enter_date  datetime (auto)
  description varchar(2048)
  notes       text
  currency    varchar(10)

Split
  id          integer PK
  guid        UUID (unique)
  transaction_id  FK → Transaction (CASCADE delete)
  account_id      FK → Account (PROTECT — won't delete account with splits)
  value_num   decimal(18,4)  — positive = debit, negative = credit
  memo        varchar(2048)
  confirmed   bool  — true = Cleared (C), toggled manually by the user
  reconciled  bool  — true = Reconciled (R), set by reconciliation workflow
  reconcile_date  date (nullable)
```

### Split CLR status

Each split carries a **cleared** status, following the GnuCash convention:

| Badge | Code | Meaning |
|-------|------|---------|
| N | `reconciled=False` | Not cleared — default state |
| C | `reconciled=False, confirmed=True` | Cleared — manually confirmed by the user |
| R | `reconciled=True` | Reconciled — locked after a reconciliation session (stores `reconcile_date`) |

Clicking the badge in the register toggles between N and C. The R state is set by the reconciliation workflow and cannot be toggled manually.

### Account tree

Accounts form an arbitrary-depth tree via the `parent` self-foreign-key. The root accounts (parent = NULL) are typically the five top-level GnuCash types. The sidebar renders the tree recursively using the `_account_node.html` partial.

## Request / response flow

```
GET /account/<id>/
  → login_required decorator checks session
  → account_register(request, account_id)
      → fetch Account by PK
      → fetch Splits for that account, ordered by post_date
      → compute running balance in Python (single pass)
      → _build_tree() for sidebar (one query, assembled in Python)
      → render register.html
```

`_build_tree()` loads all accounts in one query, builds a dict keyed by PK, then attaches children to parents in O(n) — no recursive SQL queries.

## Authentication

Django's built-in `auth` framework is used unchanged. Two users (`husband` and `wife`) share a single ledger — there is no per-user data isolation. `@login_required` is applied to every view except login/logout. Session cookies expire on browser close (Django default).

## GnuCash importer

See [gnucash-import.md](gnucash-import.md) for details. The importer is a standalone module (`ledger/gnucash_import.py`) with no Django model imports at module level, so it can be tested without the ORM if needed.

## Static files

No build pipeline. All CSS is inline in `base.html` using CSS custom properties (variables) for theming. No JavaScript frameworks are used.
