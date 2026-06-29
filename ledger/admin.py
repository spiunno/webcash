from django.contrib import admin
from .models import Account, Transaction, Split, Price


class SplitInline(admin.TabularInline):
    model = Split
    extra = 0
    fields = ('account', 'value_num', 'memo', 'reconciled')


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ('name', 'account_type', 'commodity_mnemonic', 'parent', 'placeholder')
    list_filter = ('account_type',)
    search_fields = ('name',)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('post_date', 'description', 'currency', 'is_balanced')
    list_filter = ('currency',)
    search_fields = ('description',)
    inlines = [SplitInline]


@admin.register(Split)
class SplitAdmin(admin.ModelAdmin):
    list_display = ('transaction', 'account', 'value_num', 'reconciled')
    list_filter = ('reconciled',)
    search_fields = ('memo',)


@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    list_display = ('date', 'commodity_mnemonic', 'currency', 'value_num')
    list_filter = ('currency', 'commodity_mnemonic')
    search_fields = ('commodity_mnemonic', 'currency')
    ordering = ('-date',)
