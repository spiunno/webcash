import uuid
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User


class Account(models.Model):
    ASSET = 'ASSET'
    LIABILITY = 'LIABILITY'
    INCOME = 'INCOME'
    EXPENSE = 'EXPENSE'
    EQUITY = 'EQUITY'

    TYPE_CHOICES = [
        (ASSET, 'Asset'),
        (LIABILITY, 'Liability'),
        (INCOME, 'Income'),
        (EXPENSE, 'Expense'),
        (EQUITY, 'Equity'),
    ]

    # GnuCash uses GUIDs for account ids; we mirror that for clean imports
    guid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    commodity_mnemonic = models.CharField(max_length=10, default='USD')
    parent = models.ForeignKey(
        'self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children'
    )
    description = models.TextField(blank=True)
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
        """Debit-positive types: ASSET, EXPENSE. Credit-positive: LIABILITY, INCOME, EQUITY."""
        return 1 if self.account_type in (self.ASSET, self.EXPENSE) else -1

    def balance(self):
        total = self.splits.aggregate(s=models.Sum('value_num'))['s'] or Decimal('0')
        return total * self.normal_balance


class Transaction(models.Model):
    guid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    post_date = models.DateField()
    enter_date = models.DateTimeField(auto_now_add=True)
    description = models.CharField(max_length=2048, blank=True)
    notes = models.TextField(blank=True)
    currency = models.CharField(max_length=10, default='USD')

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
    # Positive value = debit; stored as exact Decimal to avoid float rounding
    value_num = models.DecimalField(max_digits=18, decimal_places=4)
    reconciled = models.BooleanField(default=False)
    reconcile_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['transaction__post_date']

    def __str__(self):
        return f'{self.account.name} {self.value_num}'
