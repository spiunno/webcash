import uuid
import json
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User


CURRENCIES = [
    ('AED', 'AED – UAE Dirham'), ('AUD', 'AUD – Australian Dollar'),
    ('BRL', 'BRL – Brazilian Real'), ('CAD', 'CAD – Canadian Dollar'),
    ('CHF', 'CHF – Swiss Franc'), ('CNY', 'CNY – Chinese Yuan'),
    ('CZK', 'CZK – Czech Koruna'), ('DKK', 'DKK – Danish Krone'),
    ('EUR', 'EUR – Euro'), ('GBP', 'GBP – British Pound'),
    ('HKD', 'HKD – Hong Kong Dollar'), ('HUF', 'HUF – Hungarian Forint'),
    ('IDR', 'IDR – Indonesian Rupiah'), ('ILS', 'ILS – Israeli Shekel'),
    ('INR', 'INR – Indian Rupee'), ('JPY', 'JPY – Japanese Yen'),
    ('KRW', 'KRW – South Korean Won'), ('MXN', 'MXN – Mexican Peso'),
    ('MYR', 'MYR – Malaysian Ringgit'), ('NOK', 'NOK – Norwegian Krone'),
    ('NZD', 'NZD – New Zealand Dollar'), ('PHP', 'PHP – Philippine Peso'),
    ('PLN', 'PLN – Polish Zloty'), ('RON', 'RON – Romanian Leu'),
    ('RUB', 'RUB – Russian Ruble'), ('SAR', 'SAR – Saudi Riyal'),
    ('SEK', 'SEK – Swedish Krona'), ('SGD', 'SGD – Singapore Dollar'),
    ('THB', 'THB – Thai Baht'), ('TRY', 'TRY – Turkish Lira'),
    ('TWD', 'TWD – Taiwan Dollar'), ('USD', 'USD – US Dollar'),
    ('ZAR', 'ZAR – South African Rand'),
]


class Account(models.Model):
    ASSET = 'ASSET'
    BANK = 'BANK'
    CASH = 'CASH'
    RECEIVABLE = 'RECEIVABLE'
    STOCK = 'STOCK'
    MUTUAL = 'MUTUAL'
    LIABILITY = 'LIABILITY'
    CREDIT = 'CREDIT'
    PAYABLE = 'PAYABLE'
    INCOME = 'INCOME'
    EXPENSE = 'EXPENSE'
    EQUITY = 'EQUITY'

    TYPE_CHOICES = [
        (ASSET,      'Asset'),
        (BANK,       'Bank'),
        (CASH,       'Cash'),
        (RECEIVABLE, 'Receivable'),
        (STOCK,      'Stock'),
        (MUTUAL,     'Fund'),
        (EQUITY,     'Equity'),
        (INCOME,     'Income'),
        (LIABILITY,  'Liability'),
        (CREDIT,     'Credit Card'),
        (PAYABLE,    'Payable'),
        (EXPENSE,    'Expense'),
    ]

    NAMESPACE_CURRENCY = 'CURRENCY'
    NAMESPACE_SECURITY = 'SECURITY'
    NAMESPACE_CHOICES = [
        (NAMESPACE_CURRENCY, 'Currency'),
        (NAMESPACE_SECURITY, 'Security'),
    ]

    FRACTION_CHOICES = [
        (1,    '1  (whole units)'),
        (10,   '1/10'),
        (100,  '1/100  (cents)'),
        (1000, '1/1000  (mils)'),
    ]

    # GnuCash uses GUIDs for account ids; we mirror that for clean imports
    guid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=32, blank=True)
    account_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    commodity_namespace = models.CharField(max_length=20, choices=NAMESPACE_CHOICES, default=NAMESPACE_CURRENCY)
    commodity_mnemonic = models.CharField(max_length=32, default='EUR')
    smallest_fraction = models.IntegerField(default=100)
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children'
    )
    description = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    placeholder = models.BooleanField(default=False)
    hidden = models.BooleanField(default=False)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.full_path()

    def full_path(self):
        if self.parent:
            return f'{self.parent.full_path()}:{self.name}'
        return self.name

    @property
    def normal_balance(self):
        """Debit-positive: asset-family + expenses. Credit-positive: liability-family + income + equity."""
        debit_types = {self.ASSET, self.BANK, self.CASH, self.RECEIVABLE, self.STOCK, self.MUTUAL, self.EXPENSE}
        return 1 if self.account_type in debit_types else -1

    def balance(self):
        from django.db.models.functions import Coalesce
        total = self.splits.aggregate(
            s=models.Sum(Coalesce('quantity_num', 'value_num'))
        )['s'] or Decimal('0')
        return total * self.normal_balance


class Transaction(models.Model):
    guid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    post_date = models.DateField()
    enter_date = models.DateTimeField(auto_now_add=True)
    description = models.CharField(max_length=2048, blank=True)
    notes = models.TextField(blank=True)
    currency = models.CharField(max_length=32, default='USD')

    class Meta:
        ordering = ['-post_date', '-enter_date']

    def __str__(self):
        return f'{self.post_date} {self.description}'

    def is_balanced(self):
        total = self.splits.aggregate(s=models.Sum('value_num'))['s'] or Decimal('0')
        return total == Decimal('0')


class Split(models.Model):
    guid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='splits')
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='splits')
    memo = models.CharField(max_length=2048, blank=True)
    # value_num: amount in the transaction's base currency (sums to 0 across all splits)
    value_num = models.DecimalField(max_digits=18, decimal_places=4)
    # quantity_num: amount in the account's native commodity (None = same as value_num)
    quantity_num = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    reconciled = models.BooleanField(default=False)
    reconcile_date = models.DateField(null=True, blank=True)
    confirmed = models.BooleanField(default=False)

    class Meta:
        ordering = ['transaction__post_date']

    def __str__(self):
        return f'{self.account.name} {self.value_num}'

    @property
    def display_amount(self):
        """Amount to show in the account's register (native commodity)."""
        return self.quantity_num if self.quantity_num is not None else self.value_num


class Price(models.Model):
    """Exchange rate or security price: 1 unit of commodity_mnemonic = value_num units of currency."""
    commodity_namespace = models.CharField(max_length=20, default='CURRENCY')
    commodity_mnemonic = models.CharField(max_length=20)
    currency = models.CharField(max_length=32)
    date = models.DateField()
    value_num = models.DecimalField(max_digits=18, decimal_places=6)
    source = models.CharField(max_length=20, default='automatic')  # 'automatic' or 'user'

    class Meta:
        unique_together = [('commodity_mnemonic', 'currency', 'date')]
        ordering = ['-date']
        indexes = [models.Index(fields=['commodity_mnemonic', 'currency', 'date'])]

    def __str__(self):
        return f'{self.date} {self.commodity_mnemonic}/{self.currency} = {self.value_num}'


class UserPreferences(models.Model):
    LANGUAGE_CHOICES = [
        ('en', 'English'),
        ('it', 'Italiano'),
        ('fr', 'Français'),
        ('de', 'Deutsch'),
        ('es', 'Español'),
    ]
    DATE_FORMAT_CHOICES = [
        ('%Y-%m-%d', 'YYYY-MM-DD  (2026-01-31)'),
        ('%d/%m/%Y', 'DD/MM/YYYY  (31/01/2026)'),
        ('%m/%d/%Y', 'MM/DD/YYYY  (01/31/2026)'),
        ('%d.%m.%Y', 'DD.MM.YYYY  (31.01.2026)'),
        ('%d %b %Y', 'DD Mon YYYY  (31 Jan 2026)'),
    ]
    DECIMAL_SEP_CHOICES = [
        ('.', 'Period  1,234.56'),
        (',', 'Comma   1.234,56'),
    ]
    ACCENT_CHOICES = [
        ('#4a9238', 'Green'),
        ('#3d8ef8', 'Blue'),
        ('#9b59b6', 'Purple'),
        ('#e67e22', 'Orange'),
        ('#e05252', 'Red'),
        ('#16a085', 'Teal'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    language = models.CharField(max_length=5, choices=LANGUAGE_CHOICES, default='en')
    date_format = models.CharField(max_length=20, choices=DATE_FORMAT_CHOICES, default='%Y-%m-%d')
    decimal_separator = models.CharField(max_length=1, choices=DECIMAL_SEP_CHOICES, default='.')
    currency_symbol = models.CharField(max_length=5, default='$')
    currency_before = models.BooleanField(default=True, help_text='Show currency symbol before the amount')
    accent_color = models.CharField(max_length=7, choices=ACCENT_CHOICES, default='#4a9238')
    show_hidden_accounts = models.BooleanField(default=False, help_text='Show hidden accounts in account pickers')

    def __str__(self):
        return f'Preferences for {self.user.username}'

    @classmethod
    def for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    def format_amount(self, value):
        """Format a Decimal value according to user preferences."""
        if self.decimal_separator == ',':
            formatted = f'{value:,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.')
        else:
            formatted = f'{value:,.2f}'
        if self.currency_before:
            return f'{self.currency_symbol}{formatted}'
        return f'{formatted} {self.currency_symbol}'


def get_price(commodity_mnemonic, target_currency, on_date=None):
    """Return price of 1 unit of commodity_mnemonic in target_currency on or before on_date.

    Tries a direct lookup first (e.g. EUR→USD), then an inverse (USD→EUR → 1/rate).
    Returns Decimal or None.
    """
    from datetime import date as date_type
    d = on_date or date_type.today()
    if commodity_mnemonic == target_currency:
        return Decimal('1')
    p = Price.objects.filter(
        commodity_mnemonic=commodity_mnemonic, currency=target_currency, date__lte=d
    ).order_by('-date').first()
    if p:
        return p.value_num
    # Inverse lookup
    p = Price.objects.filter(
        commodity_mnemonic=target_currency, currency=commodity_mnemonic, date__lte=d
    ).order_by('-date').first()
    if p and p.value_num:
        return Decimal('1') / p.value_num
    return None


class ImportJob(models.Model):
    PENDING = 'pending'
    RUNNING = 'running'
    DONE    = 'done'
    ERROR   = 'error'
    STATUS_CHOICES = [(PENDING, 'Pending'), (RUNNING, 'Running'), (DONE, 'Done'), (ERROR, 'Error')]

    GNUCASH = 'gnucash'
    FINECO  = 'fineco'
    AMEX    = 'amex'
    KIND_CHOICES = [
        (GNUCASH, 'GnuCash file'),
        (FINECO, 'Fineco statement (PDF)'),
        (AMEX, 'American Express statement (PDF)'),
    ]

    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='import_jobs')
    kind       = models.CharField(max_length=20, choices=KIND_CHOICES, default=GNUCASH)
    created_at = models.DateTimeField(auto_now_add=True)
    status     = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    phase      = models.CharField(max_length=100, blank=True)
    progress   = models.IntegerField(default=0)
    total      = models.IntegerField(default=0)
    result_json = models.TextField(blank=True)  # JSON stats on success
    error_msg  = models.TextField(blank=True)

    def set_result(self, stats):
        self.result_json = json.dumps(stats)

    def get_result(self):
        return json.loads(self.result_json) if self.result_json else {}

    class Meta:
        ordering = ['-created_at']


class SystemSettings(models.Model):
    """Singleton model for application-wide configuration."""
    exchange_rate_api_key = models.CharField(
        max_length=200, blank=True,
        help_text='API key for a paid exchange-rate provider (leave blank to use the free Frankfurter API)',
    )
    exchange_rate_provider = models.CharField(
        max_length=50, blank=True, default='frankfurter',
        choices=[
            ('frankfurter', 'Frankfurter (free, no key needed)'),
            ('exchangeratesapi', 'ExchangeRatesAPI.io (key required)'),
            ('openexchangerates', 'Open Exchange Rates (key required)'),
        ],
    )
    base_currency = models.CharField(max_length=3, default='EUR')
    maintenance_mode = models.BooleanField(default=False)
    registration_open = models.BooleanField(default=False, help_text='Allow new users to self-register')
    notes = models.TextField(blank=True, help_text='Internal admin notes')

    class Meta:
        verbose_name = 'System Settings'
        verbose_name_plural = 'System Settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return 'System Settings'
