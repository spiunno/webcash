from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.contrib import messages

from .models import Account, Transaction, Split, UserPreferences
from .gnucash_import import import_file


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    error = None
    if request.method == 'POST':
        user = authenticate(request,
                            username=request.POST.get('username'),
                            password=request.POST.get('password'))
        if user:
            login(request, user)
            return redirect(request.GET.get('next', 'dashboard'))
        error = 'Invalid username or password.'
    return render(request, 'ledger/login.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


# ---------------------------------------------------------------------------
# Dashboard / summary
# ---------------------------------------------------------------------------

@login_required
def dashboard(request):
    def sum_type(account_type):
        result = (
            Split.objects.filter(account__account_type=account_type)
            .aggregate(total=Sum('value_num'))['total'] or Decimal('0')
        )
        # ASSET/EXPENSE are debit-normal (positive = good); others are credit-normal
        if account_type in (Account.ASSET, Account.EXPENSE):
            return result
        return -result

    assets = sum_type(Account.ASSET)
    liabilities = sum_type(Account.LIABILITY)
    income = sum_type(Account.INCOME)
    expenses = sum_type(Account.EXPENSE)
    net_worth = assets - liabilities

    account_tree = _build_tree()
    recent_txns = Transaction.objects.prefetch_related('splits__account').order_by('-post_date', '-enter_date')[:20]

    return render(request, 'ledger/dashboard.html', {
        'assets': assets,
        'liabilities': liabilities,
        'income': income,
        'expenses': expenses,
        'net_worth': net_worth,
        'account_tree': account_tree,
        'recent_txns': recent_txns,
    })


# ---------------------------------------------------------------------------
# Account register
# ---------------------------------------------------------------------------

@login_required
def account_register(request, account_id):
    account = get_object_or_404(Account, pk=account_id)
    account_tree = _build_tree()

    splits = (
        Split.objects
        .filter(account=account)
        .select_related('transaction')
        .prefetch_related('transaction__splits__account')
        .order_by('transaction__post_date', 'transaction__enter_date')
    )

    # Compute running balance
    rows = []
    running = Decimal('0')
    for split in splits:
        running += split.value_num * account.normal_balance
        # Determine the "other side" label
        other_splits = [s for s in split.transaction.splits.all() if s.pk != split.pk]
        if len(other_splits) == 1:
            other_label = other_splits[0].account.name
        elif len(other_splits) > 1:
            other_label = 'Split transaction'
        else:
            other_label = '—'

        rows.append({
            'split': split,
            'txn': split.transaction,
            'other_label': other_label,
            'running': running,
        })

    return render(request, 'ledger/register.html', {
        'account': account,
        'rows': rows,
        'account_tree': account_tree,
    })


# ---------------------------------------------------------------------------
# GnuCash import
# ---------------------------------------------------------------------------

@login_required
def import_gnucash(request):
    account_tree = _build_tree()
    if request.method == 'POST' and request.FILES.get('gnucash_file'):
        uploaded = request.FILES['gnucash_file']
        import tempfile, os
        with tempfile.NamedTemporaryFile(delete=False, suffix='.gnucash') as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk)
            tmp_path = tmp.name
        try:
            stats = import_file(tmp_path)
            messages.success(
                request,
                f"Import complete: {stats['accounts_created']} accounts, "
                f"{stats['transactions_created']} transactions created; "
                f"{stats['accounts_skipped']} accounts, "
                f"{stats['transactions_skipped']} transactions already existed."
            )
        except Exception as exc:
            messages.error(request, f'Import failed: {exc}')
        finally:
            os.unlink(tmp_path)
        return redirect('dashboard')

    return render(request, 'ledger/import.html', {'account_tree': account_tree})


# ---------------------------------------------------------------------------
# Transaction edit
# ---------------------------------------------------------------------------

@login_required
def transaction_edit(request, txn_id):
    txn = get_object_or_404(Transaction, pk=txn_id)
    all_accounts = Account.objects.order_by('account_type', 'name')
    account_tree = _build_tree()
    back = request.GET.get('back', '')

    errors = []
    if request.method == 'POST':
        post_date = request.POST.get('post_date', '').strip()
        description = request.POST.get('description', '').strip()

        # Collect splits: fields named split_N_account, split_N_value, split_N_memo
        split_data = []
        i = 0
        while True:
            account_id = request.POST.get(f'split_{i}_account')
            if account_id is None:
                break
            value_str = request.POST.get(f'split_{i}_value', '').strip()
            memo = request.POST.get(f'split_{i}_memo', '').strip()
            split_data.append((account_id, value_str, memo))
            i += 1

        # Validate
        if not post_date:
            errors.append('Date is required.')
        if len(split_data) < 2:
            errors.append('A transaction needs at least two splits.')

        parsed_splits = []
        total = Decimal('0')
        for idx, (account_id, value_str, memo) in enumerate(split_data):
            try:
                account = Account.objects.get(pk=account_id)
            except Account.DoesNotExist:
                errors.append(f'Split {idx+1}: invalid account.')
                continue
            try:
                value = Decimal(value_str.replace(',', '.'))
            except Exception:
                errors.append(f'Split {idx+1}: invalid amount "{value_str}".')
                continue
            total += value
            parsed_splits.append((account, value, memo))

        if not errors and total != Decimal('0'):
            errors.append(f'Splits are not balanced (sum = {total}). Debits must equal credits.')

        if not errors:
            from datetime import date as date_type
            txn.post_date = date_type.fromisoformat(post_date)
            txn.description = description
            txn.save()
            txn.splits.all().delete()
            for account, value, memo in parsed_splits:
                Split.objects.create(transaction=txn, account=account, value_num=value, memo=memo)
            messages.success(request, 'Transaction saved.')
            if request.GET.get('partial') or request.POST.get('partial'):
                from django.http import JsonResponse
                return JsonResponse({'ok': True})
            return redirect(back or 'dashboard')

    splits = list(txn.splits.select_related('account').all())
    ctx = {
        'txn': txn,
        'splits': splits,
        'all_accounts': all_accounts,
        'account_tree': account_tree,
        'errors': errors,
        'back': back,
    }
    if request.GET.get('partial') or request.POST.get('partial'):
        return render(request, 'ledger/transaction_edit_partial.html', ctx)
    return render(request, 'ledger/transaction_edit.html', ctx)


# ---------------------------------------------------------------------------
# New transaction
# ---------------------------------------------------------------------------

@login_required
def transaction_new(request, account_id):
    account = get_object_or_404(Account, pk=account_id)
    all_accounts = Account.objects.order_by('account_type', 'name')
    account_tree = _build_tree()
    errors = []

    if request.method == 'POST':
        from datetime import date as date_type
        post_date_str = request.POST.get('post_date', '').strip()
        description = request.POST.get('description', '').strip()
        mode = request.POST.get('mode', 'simple')

        try:
            post_date = date_type.fromisoformat(post_date_str)
        except ValueError:
            errors.append('Invalid date.')
            post_date = None

        if mode == 'simple' and post_date and not errors:
            to_account_id = request.POST.get('to_account', '').strip()
            debit_str = request.POST.get('debit', '').strip() or '0'
            credit_str = request.POST.get('credit', '').strip() or '0'
            memo = request.POST.get('memo', '').strip()
            try:
                amount = Decimal(debit_str.replace(',', '.')) - Decimal(credit_str.replace(',', '.'))
            except Exception:
                errors.append('Invalid amount.')
                amount = Decimal('0')
            if not to_account_id:
                errors.append('Please select a destination account.')
            if amount == Decimal('0') and not errors:
                errors.append('Please enter an amount.')
            if not errors:
                try:
                    to_account = Account.objects.get(pk=to_account_id)
                    txn = Transaction.objects.create(
                        post_date=post_date, description=description,
                        currency=account.commodity_mnemonic,
                    )
                    Split.objects.create(transaction=txn, account=account, value_num=amount, memo=memo)
                    Split.objects.create(transaction=txn, account=to_account, value_num=-amount)
                    request.session['last_txn_date'] = post_date_str
                    if request.GET.get('partial') or request.POST.get('partial'):
                        from django.http import JsonResponse
                        return JsonResponse({'ok': True})
                    return redirect('account_register', account_id=account_id)
                except Account.DoesNotExist:
                    errors.append('Invalid destination account.')

        elif mode == 'multi' and post_date and not errors:
            split_data = []
            i = 0
            while True:
                a_id = request.POST.get(f'split_{i}_account')
                if a_id is None:
                    break
                split_data.append((a_id, request.POST.get(f'split_{i}_value', '').strip(),
                                   request.POST.get(f'split_{i}_memo', '').strip()))
                i += 1
            if len(split_data) < 2:
                errors.append('A transaction needs at least two splits.')
            parsed_splits = []
            total = Decimal('0')
            for idx, (a_id, value_str, memo) in enumerate(split_data):
                try:
                    acc = Account.objects.get(pk=a_id)
                except Account.DoesNotExist:
                    errors.append(f'Split {idx+1}: invalid account.'); continue
                try:
                    value = Decimal(value_str.replace(',', '.'))
                except Exception:
                    errors.append(f'Split {idx+1}: invalid amount.'); continue
                total += value
                parsed_splits.append((acc, value, memo))
            if not errors and total != Decimal('0'):
                errors.append(f'Splits not balanced (sum = {total}).')
            if not errors:
                txn = Transaction.objects.create(
                    post_date=post_date, description=description,
                    currency=account.commodity_mnemonic,
                )
                for acc, value, memo in parsed_splits:
                    Split.objects.create(transaction=txn, account=acc, value_num=value, memo=memo)
                request.session['last_txn_date'] = post_date_str
                if request.GET.get('partial') or request.POST.get('partial'):
                    from django.http import JsonResponse
                    return JsonResponse({'ok': True})
                return redirect('account_register', account_id=account_id)

    from datetime import date as date_type
    default_date = request.session.get('last_txn_date') or date_type.today().isoformat()
    ctx = {
        'account': account,
        'all_accounts': all_accounts,
        'account_tree': account_tree,
        'errors': errors,
        'default_date': default_date,
    }
    if request.GET.get('partial') or request.POST.get('partial'):
        return render(request, 'ledger/transaction_new_partial.html', ctx)
    return render(request, 'ledger/transaction_new_partial.html', ctx)


@login_required
def transaction_autocomplete(request, account_id):
    from django.http import JsonResponse
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    splits = (
        Split.objects
        .filter(account_id=account_id, transaction__description__icontains=q)
        .select_related('transaction')
        .prefetch_related('transaction__splits__account')
        .order_by('-transaction__post_date')
    )
    seen = {}
    for split in splits:
        txn = split.transaction
        if txn.description in seen:
            continue
        all_sp = list(txn.splits.all())
        other = [s for s in all_sp if s.account_id != account_id]
        entry = {'description': txn.description, 'multi': len(other) != 1}
        if len(other) == 1:
            entry.update({
                'account_id': other[0].account_id,
                'account_name': other[0].account.name,
                'amount': f'{abs(split.value_num):.2f}',
                'debit': bool(split.value_num > 0),
            })
        seen[txn.description] = entry
        if len(seen) >= 8:
            break
    return JsonResponse({'results': list(seen.values())})


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

@login_required
def preferences(request):
    account_tree = _build_tree()
    prefs = UserPreferences.for_user(request.user)
    saved = False
    if request.method == 'POST':
        prefs.language = request.POST.get('language', prefs.language)
        prefs.date_format = request.POST.get('date_format', prefs.date_format)
        prefs.decimal_separator = request.POST.get('decimal_separator', prefs.decimal_separator)
        prefs.currency_symbol = request.POST.get('currency_symbol', prefs.currency_symbol)[:5]
        prefs.currency_before = request.POST.get('currency_before') == '1'
        prefs.accent_color = request.POST.get('accent_color', prefs.accent_color)
        prefs.save()
        saved = True
    return render(request, 'ledger/preferences.html', {
        'prefs': prefs,
        'account_tree': account_tree,
        'saved': saved,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_tree():
    """Return top-level accounts with nested children attached as .child_list.

    GnuCash files contain a single invisible ROOT account at the top level.
    If the only null-parent account is a placeholder (or has type ROOT mapped
    to ASSET with name 'Root Account'), we skip it and promote its children,
    so the sidebar starts at the real Assets/Liabilities/… level.
    """
    all_accounts = list(Account.objects.all())
    by_id = {a.pk: a for a in all_accounts}
    for a in all_accounts:
        a.child_list = []
    raw_roots = []
    for a in all_accounts:
        if a.parent_id and a.parent_id in by_id:
            by_id[a.parent_id].child_list.append(a)
        elif a.parent_id is None:
            raw_roots.append(a)

    # Unwrap the GnuCash invisible root: one placeholder root whose children
    # are the real top-level accounts.
    if len(raw_roots) == 1 and (raw_roots[0].placeholder or raw_roots[0].name == 'Root Account'):
        return raw_roots[0].child_list
    return raw_roots
