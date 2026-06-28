from django.core.management.base import BaseCommand
from decimal import Decimal
import urllib.request
import json
from datetime import date, timedelta
from ledger.models import Price


class Command(BaseCommand):
    help = 'Download currency exchange rates from api.frankfurter.app (ECB data)'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90,
                            help='Number of past days to download (default: 90)')
        parser.add_argument('--base', type=str, default='EUR',
                            help='Base currency (default: EUR)')

    def handle(self, *args, **options):
        days = options['days']
        base = options['base'].upper()
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        url = f'https://api.frankfurter.app/{start_date}..{end_date}?from={base}'
        self.stdout.write(f'Downloading {base} rates {start_date} → {end_date}…')
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'WebCash/1.0'})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Download failed: {e}'))
            return
        rates_by_date = data.get('rates', {})
        if not rates_by_date:
            self.stdout.write(self.style.WARNING('No rates returned.'))
            return
        created = updated = 0
        for date_str, rates in rates_by_date.items():
            for currency, value in rates.items():
                _, is_new = Price.objects.update_or_create(
                    commodity_mnemonic=base,
                    currency=currency,
                    date=date_str,
                    defaults={
                        'value_num': Decimal(str(value)),
                        'source': 'automatic',
                        'commodity_namespace': 'CURRENCY',
                    },
                )
                if is_new:
                    created += 1
                else:
                    updated += 1
        self.stdout.write(self.style.SUCCESS(
            f'Done: {created} created, {updated} updated across {len(rates_by_date)} trading days.'
        ))
