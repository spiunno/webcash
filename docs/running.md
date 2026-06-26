# Running WebCash

## Development server

```bash
source .venv/bin/activate      # if not already active
python manage.py runserver
```

The server starts at **http://127.0.0.1:8000**. Django's auto-reloader watches for file changes and restarts automatically.

To bind to a different address or port:

```bash
python manage.py runserver 0.0.0.0:8080
```

## Logging in

| URL | Purpose |
|---|---|
| `http://127.0.0.1:8000/login/` | Sign in |
| `http://127.0.0.1:8000/` | Dashboard (redirects to login if not authenticated) |
| `http://127.0.0.1:8000/import/` | GnuCash XML import |
| `http://127.0.0.1:8000/account/<id>/` | Transaction register for one account |
| `http://127.0.0.1:8000/admin/` | Django admin (superuser only) |

## Common management commands

```bash
# Apply new migrations after a model change
python manage.py migrate

# Open an interactive Python shell with Django context
python manage.py shell

# Run the test suite
python manage.py test ledger

# Create a new database migration after editing models.py
python manage.py makemigrations ledger

# Reset the database (destructive — deletes all data)
rm db.sqlite3
python manage.py migrate
```

## Resetting a user password

```bash
python manage.py changepassword husband
```

## Backing up the database

The entire ledger lives in `db.sqlite3`. Copy it anywhere:

```bash
cp db.sqlite3 db.sqlite3.bak-$(date +%Y%m%d)
```

To restore, stop the server and copy the backup back.

## Production deployment (brief notes)

The development server is not suitable for production. For a home-network
deployment behind a reverse proxy (e.g. nginx or Caddy):

1. Install `gunicorn`:  `pip install gunicorn`
2. Set `DEBUG = False` and configure `ALLOWED_HOSTS` in `webcash/settings.py`
3. Generate a new `SECRET_KEY` and store it in an environment variable
4. Run: `gunicorn webcash.wsgi:application --bind 127.0.0.1:8000`
5. Serve static files directly from nginx; run `python manage.py collectstatic` first

Full production hardening is out of scope for this document — see the
[Django deployment checklist](https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/).
