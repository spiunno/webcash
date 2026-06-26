# WebCash Documentation

WebCash is a self-hosted family finance ledger built with Django and SQLite.
It follows the GnuCash double-entry bookkeeping model and can import GnuCash XML files directly.

## Contents

| Document | What it covers |
|---|---|
| [architecture.md](architecture.md) | System design, data model, request flow |
| [install.md](install.md) | Prerequisites and first-time setup |
| [running.md](running.md) | Starting the server, day-to-day commands |
| [gnucash-import.md](gnucash-import.md) | How the GnuCash XML importer works |

## Quick orientation

```
webcash/          ← Django project package (settings, root URLs)
ledger/           ← Single Django app (models, views, importer)
templates/ledger/ ← HTML templates
docs/             ← This documentation tree
db.sqlite3        ← SQLite database (created on first migrate)
```
