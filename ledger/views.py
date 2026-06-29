from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.contrib import messages

from .models import Account, Transaction, Split, UserPreferences, Price, CURRENCIES, get_price
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
    ASSET_TYPES = [Account.ASSET, Account.BANK, Account.CASH, Account.RECEIVABLE,
                   Account.STOCK, Account.MUTUAL]
    LIAB_TYPES  = [Account.LIABILITY, Account.CREDIT, Account.PAYABLE]

    def sum_types(types, negate=False):
        result = (
            Split.objects.filter(account__account_type__in=types)
            .aggregate(total=Sum('value_num'))['total'] or Decimal('0')
        )
        return -result if negate else result

    assets     = sum_types(ASSET_TYPES)
    liabilities = sum_types(LIAB_TYPES, negate=True)
    income     = sum_types([Account.INCOME], negate=True)
    expenses   = sum_types([Account.EXPENSE])
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
        display_amount = split.quantity_num if split.quantity_num is not None else split.value_num
        running += display_amount * account.normal_balance
        # Determine the "other side" label
        other_splits = [s for s in split.transaction.splits.all() if s.pk != split.pk]
        if len(other_splits) == 1:
            other_label = other_splits[0].account.name
        elif len(other_splits) > 1:
            other_label = 'Split transaction'
        else:
            other_label = '—'

        other_account_id = other_splits[0].account_id if len(other_splits) == 1 else None
        rows.append({
            'split': split,
            'txn': split.transaction,
            'other_label': other_label,
            'other_account_id': other_account_id,
            'running': running,
            'display_amount': display_amount,
        })

    import math as _math
    sf = account.smallest_fraction or 100
    dp = round(_math.log10(sf)) if sf > 1 else 0
    # Build a prefs-like object carrying decimal_places for this account's fraction
    base_prefs = UserPreferences.for_user(request.user)
    class _AccountPrefs:
        decimal_separator = base_prefs.decimal_separator
        decimal_places    = dp
    account_prefs = _AccountPrefs()

    return render(request, 'ledger/register.html', {
        'account': account,
        'rows': rows,
        'account_tree': account_tree,
        'account_prefs': account_prefs,
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
# Transaction delete / duplicate
# ---------------------------------------------------------------------------

@login_required
def transaction_delete(request, txn_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    txn = get_object_or_404(Transaction, pk=txn_id)
    back = request.POST.get('back', '')
    if txn.splits.filter(reconciled=True).exists():
        messages.error(request, 'This transaction cannot be deleted because it contains reconciled splits.')
        if back:
            return redirect(back)
        return redirect('dashboard')
    txn.delete()
    if back:
        return redirect(back)
    return redirect('dashboard')


@login_required
def transaction_duplicate(request, txn_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    from datetime import date
    txn = get_object_or_404(Transaction, pk=txn_id)
    back = request.POST.get('back', '')
    new_txn = Transaction.objects.create(
        post_date=date.today(),
        description=txn.description,
        notes=txn.notes,
        currency=txn.currency,
    )
    for sp in txn.splits.all():
        Split.objects.create(
            transaction=new_txn,
            account=sp.account,
            memo=sp.memo,
            value_num=sp.value_num,
            value_denom=sp.value_denom,
            quantity_num=sp.quantity_num,
            quantity_denom=sp.quantity_denom,
        )
    if back:
        return redirect(back)
    return redirect('dashboard')


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

        if not errors and txn.splits.filter(reconciled=True).exists():
            errors.append('This transaction cannot be edited because it contains reconciled splits.')

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
                    # Cross-currency: compute or accept explicit quantity for the to-account
                    from_ccy = account.commodity_mnemonic
                    to_ccy = to_account.commodity_mnemonic
                    to_quantity = None
                    if from_ccy != to_ccy:
                        to_qty_str = request.POST.get('to_quantity', '').strip()
                        if to_qty_str:
                            try:
                                to_quantity = Decimal(to_qty_str.replace(',', '.'))
                            except Exception:
                                pass
                        if to_quantity is None:
                            rate = get_price(from_ccy, to_ccy, post_date)
                            if rate:
                                to_quantity = -amount * rate
                    txn = Transaction.objects.create(
                        post_date=post_date, description=description,
                        currency=account.commodity_mnemonic,
                    )
                    Split.objects.create(transaction=txn, account=account, value_num=amount, memo=memo)
                    Split.objects.create(transaction=txn, account=to_account, value_num=-amount,
                                         quantity_num=to_quantity)
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
# Reconciliation
# ---------------------------------------------------------------------------

def _get_account_ids(account, include_subaccounts=False):
    ids = [account.pk]
    if include_subaccounts:
        def collect(acc):
            for child in acc.children.all():
                ids.append(child.pk)
                collect(child)
        collect(account)
    return ids


@login_required
def reconcile_start(request, account_id):
    if request.method != 'POST':
        return redirect('account_register', account_id=account_id)
    statement_date = request.POST.get('statement_date', '').strip()
    ending_balance = request.POST.get('ending_balance', '').strip()
    include_subaccounts = request.POST.get('include_subaccounts') == 'on'
    from datetime import date as date_type
    try:
        date_type.fromisoformat(statement_date)
        Decimal(ending_balance.replace(',', '.'))
    except Exception:
        messages.error(request, 'Invalid date or ending balance.')
        return redirect('account_register', account_id=account_id)
    request.session[f'reconcile_{account_id}'] = {
        'statement_date': statement_date,
        'ending_balance': ending_balance,
        'include_subaccounts': include_subaccounts,
    }
    return redirect('reconcile_view', account_id=account_id)


@login_required
def reconcile_view(request, account_id):
    account = get_object_or_404(Account, pk=account_id)
    account_tree = _build_tree()
    session_key = f'reconcile_{account_id}'
    params = request.session.get(session_key)
    if not params:
        messages.error(request, 'No reconciliation in progress.')
        return redirect('account_register', account_id=account_id)

    statement_date = params['statement_date']
    ending_balance = Decimal(params['ending_balance'].replace(',', '.'))
    include_subaccounts = params.get('include_subaccounts', False)

    account_ids = _get_account_ids(account, include_subaccounts)

    from django.db.models.functions import Coalesce as _C
    starting_balance = (
        Split.objects.filter(account_id__in=account_ids, reconciled=True)
        .aggregate(s=Sum(_C('quantity_num', 'value_num')))['s'] or Decimal('0')
    ) * account.normal_balance

    splits = (
        Split.objects
        .filter(account_id__in=account_ids, reconciled=False,
                transaction__post_date__lte=statement_date)
        .select_related('transaction', 'account')
        .order_by('transaction__post_date', 'transaction__enter_date')
    )

    base_prefs = UserPreferences.for_user(request.user)
    funds_in = []
    funds_out = []
    for split in splits:
        amount = split.quantity_num if split.quantity_num is not None else split.value_num
        display = amount * account.normal_balance
        item = {
            'split': split,
            'txn': split.transaction,
            'display': display,
            'abs_display': abs(display),
        }
        if display >= 0:
            funds_in.append(item)
        else:
            funds_out.append(item)

    return render(request, 'ledger/reconcile.html', {
        'account': account,
        'account_tree': account_tree,
        'statement_date': statement_date,
        'ending_balance': ending_balance,
        'starting_balance': starting_balance,
        'funds_in': funds_in,
        'funds_out': funds_out,
        'prefs': base_prefs,
    })


@login_required
def reconcile_done(request, account_id):
    if request.method != 'POST':
        return redirect('account_register', account_id=account_id)
    account = get_object_or_404(Account, pk=account_id)
    session_key = f'reconcile_{account_id}'
    params = request.session.get(session_key)
    if not params:
        return redirect('account_register', account_id=account_id)
    statement_date = params['statement_date']
    selected_ids = request.POST.getlist('selected_splits')
    from datetime import date as date_type
    rec_date = date_type.fromisoformat(statement_date)
    updated = Split.objects.filter(pk__in=selected_ids).update(
        reconciled=True, reconcile_date=rec_date, confirmed=True
    )
    del request.session[session_key]
    messages.success(request, f'Reconciliation complete — {updated} transaction(s) marked as reconciled.')
    return redirect('account_register', account_id=account_id)


@login_required
def reconcile_postpone(request, account_id):
    messages.info(request, 'Reconciliation postponed. Your progress has been saved.')
    return redirect('account_register', account_id=account_id)


@login_required
def reconcile_cancel(request, account_id):
    session_key = f'reconcile_{account_id}'
    request.session.pop(session_key, None)
    return redirect('account_register', account_id=account_id)


@login_required
def split_toggle_confirmed(request, split_id):
    if request.method != 'POST':
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(['POST'])
    split = get_object_or_404(Split, pk=split_id)
    if split.reconciled:
        from django.http import JsonResponse
        return JsonResponse({'error': 'reconciled', 'confirmed': True}, status=400)
    split.confirmed = not split.confirmed
    split.save(update_fields=['confirmed'])
    from django.http import JsonResponse
    return JsonResponse({'confirmed': split.confirmed})


# ---------------------------------------------------------------------------
# Accounts Overview
# ---------------------------------------------------------------------------

@login_required
def accounts_overview(request):
    from collections import defaultdict
    all_accounts = list(Account.objects.all())

    # Direct balances per account — use quantity_num (native commodity) when available
    from django.db.models.functions import Coalesce as _Coalesce
    from django.db.models import F as _F
    direct = defaultdict(lambda: Decimal('0'))
    for row in Split.objects.values('account_id').annotate(
        total=Sum(_Coalesce('quantity_num', 'value_num'))
    ):
        direct[row['account_id']] = row['total'] or Decimal('0')

    # Build tree and compute cumulative balances bottom-up
    by_id = {a.pk: a for a in all_accounts}
    for a in all_accounts:
        a.child_list = []           # must be a separate pass before appending
    children_map = defaultdict(list)
    for a in all_accounts:
        if a.parent_id and a.parent_id in by_id:
            children_map[a.parent_id].append(a.pk)
            by_id[a.parent_id].child_list.append(a)

    cum_cache = {}
    def cum_balance(acc_id):
        if acc_id in cum_cache:
            return cum_cache[acc_id]
        total = direct[acc_id]
        for child_id in children_map[acc_id]:
            total += cum_balance(child_id)
        cum_cache[acc_id] = total
        return total

    for a in all_accounts:
        a.cum_balance = cum_balance(a.pk) * a.normal_balance

    # Flatten tree with depth
    def flatten(nodes, depth=0):
        result = []
        for node in sorted(nodes, key=lambda x: x.name):
            result.append({'account': node, 'depth': depth, 'has_children': bool(node.child_list)})
            if node.child_list:
                result.extend(flatten(node.child_list, depth + 1))
        return result

    raw_roots = [a for a in all_accounts if a.parent_id is None]
    if len(raw_roots) == 1 and (raw_roots[0].placeholder or raw_roots[0].name == 'Root Account'):
        roots = raw_roots[0].child_list
    else:
        roots = raw_roots

    flat = flatten(sorted(roots, key=lambda x: x.name))

    return render(request, 'ledger/accounts.html', {
        'flat': flat,
        'account_tree': [],  # no sidebar needed
    })


# ---------------------------------------------------------------------------
# Price list / exchange rates
# ---------------------------------------------------------------------------

@login_required
def price_list(request):
    from django.db.models import Max
    latest = (
        Price.objects.values('commodity_mnemonic', 'currency')
        .annotate(latest_date=Max('date'))
        .order_by('commodity_mnemonic', 'currency')
    )
    rows = []
    for item in latest:
        p = Price.objects.filter(
            commodity_mnemonic=item['commodity_mnemonic'],
            currency=item['currency'],
            date=item['latest_date'],
        ).first()
        if p:
            rows.append(p)
    return render(request, 'ledger/prices.html', {'rows': rows})


@login_required
def update_prices_ajax(request):
    from django.http import JsonResponse
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    base = request.POST.get('base', 'EUR').upper()
    try:
        days = int(request.POST.get('days', '90'))
    except ValueError:
        days = 90
    from datetime import date as date_type, timedelta
    import urllib.request, json
    end_date = date_type.today()
    start_date = end_date - timedelta(days=days)
    url = f'https://api.frankfurter.app/{start_date}..{end_date}?from={base}'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WebCash/1.0'})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=502)
    rates_by_date = data.get('rates', {})
    created = updated = 0
    for date_str, rates in rates_by_date.items():
        for currency, value in rates.items():
            _, is_new = Price.objects.update_or_create(
                commodity_mnemonic=base,
                currency=currency,
                date=date_str,
                defaults={'value_num': Decimal(str(value)), 'source': 'automatic',
                          'commodity_namespace': 'CURRENCY'},
            )
            if is_new:
                created += 1
            else:
                updated += 1
    return JsonResponse({
        'created': created,
        'updated': updated,
        'dates': len(rates_by_date),
        'message': f'{created} new, {updated} updated across {len(rates_by_date)} trading days.',
    })


@login_required
def saved_reports(request):
    account_tree = _build_tree()
    return render(request, 'ledger/saved_reports.html', {'account_tree': account_tree})


@login_required
def networth_report(request):
    import json as _json
    account_tree = _build_tree()
    all_accounts = list(Account.objects.all())

    ASSET_TYPES  = {Account.ASSET, Account.BANK, Account.CASH, Account.RECEIVABLE, Account.STOCK, Account.MUTUAL}
    LIAB_TYPES   = {Account.LIABILITY, Account.CREDIT, Account.PAYABLE}
    EQUITY_TYPES = {Account.EQUITY}

    accounts_json = []
    for a in all_accounts:
        if a.account_type in ASSET_TYPES:
            cat = 'asset'
        elif a.account_type in LIAB_TYPES:
            cat = 'liability'
        elif a.account_type in EQUITY_TYPES:
            cat = 'equity'
        else:
            cat = 'other'
        accounts_json.append({
            'id': a.pk,
            'name': a.name,
            'type': a.account_type,
            'category': cat,
            'parent': a.parent_id,
            'hidden': a.hidden,
            'placeholder': a.placeholder,
            'commodity': a.commodity_mnemonic,
        })

    prefs = UserPreferences.for_user(request.user)
    return render(request, 'ledger/networth_report.html', {
        'account_tree': account_tree,
        'accounts_json': _json.dumps(accounts_json),
        'currencies': CURRENCIES,
        'prefs': prefs,
    })


@login_required
def networth_report_data(request):
    from django.http import JsonResponse
    from datetime import date as date_type
    from calendar import monthrange
    from collections import defaultdict
    from django.db.models.functions import Coalesce

    start_str = request.GET.get('start', '')
    end_str   = request.GET.get('end', '')
    step      = request.GET.get('step', 'month')
    report_currency = request.GET.get('currency', 'EUR').upper()
    account_ids_str = request.GET.get('accounts', '')

    try:
        start = date_type.fromisoformat(start_str)
        end   = date_type.fromisoformat(end_str)
    except ValueError:
        return JsonResponse({'error': 'Invalid dates'}, status=400)
    if start > end:
        return JsonResponse({'error': 'Start must be before end'}, status=400)

    account_ids = [int(x) for x in account_ids_str.split(',') if x.strip().isdigit()] if account_ids_str.strip() else []
    if not account_ids:
        return JsonResponse({'dates': [], 'assets': [], 'liabilities': [], 'net_worth': [], 'currency': report_currency})

    ASSET_TYPES = {Account.ASSET, Account.BANK, Account.CASH, Account.RECEIVABLE, Account.STOCK, Account.MUTUAL}
    LIAB_TYPES  = {Account.LIABILITY, Account.CREDIT, Account.PAYABLE}

    accounts = list(Account.objects.filter(pk__in=account_ids))

    # Generate date series (cap at 500 points)
    dates, current = [], start
    while current <= end and len(dates) < 500:
        dates.append(current)
        current = _next_report_date(current, step)

    # Prefetch all splits for selected accounts (single query)
    splits_by_account = defaultdict(list)
    for s in (Split.objects
              .filter(account_id__in=account_ids)
              .select_related('transaction')
              .order_by('transaction__post_date')):
        amt = s.quantity_num if s.quantity_num is not None else s.value_num
        splits_by_account[s.account_id].append((s.transaction.post_date, amt))

    # Cache exchange rates to avoid repeated DB lookups
    price_cache = {}
    def cached_price(mnemonic, d):
        if mnemonic == report_currency:
            return Decimal('1')
        key = (mnemonic, d)
        if key not in price_cache:
            price_cache[key] = get_price(mnemonic, report_currency, d) or Decimal('1')
        return price_cache[key]

    result = {'dates': [], 'assets': [], 'liabilities': [], 'net_worth': []}
    for d in dates:
        assets_total = Decimal('0')
        liab_total   = Decimal('0')
        for acc in accounts:
            native_bal = sum(amt for dt, amt in splits_by_account[acc.pk] if dt <= d) * acc.normal_balance
            converted  = native_bal * cached_price(acc.commodity_mnemonic, d)
            if acc.account_type in ASSET_TYPES:
                assets_total += converted
            elif acc.account_type in LIAB_TYPES:
                liab_total += converted
        result['dates'].append(str(d))
        result['assets'].append(round(float(assets_total), 2))
        result['liabilities'].append(round(float(liab_total), 2))
        result['net_worth'].append(round(float(assets_total - liab_total), 2))

    result['currency'] = report_currency
    return JsonResponse(result)


def _next_report_date(d, step):
    from datetime import timedelta
    from calendar import monthrange
    if step == 'day':   return d + timedelta(days=1)
    if step == 'week':  return d + timedelta(weeks=1)
    if step == '2week': return d + timedelta(weeks=2)
    months = {'month': 1, 'quarter': 3, 'halfyear': 6, 'year': 12}.get(step, 1)
    m = d.month + months
    y = d.year + (m - 1) // 12
    m = (m - 1) % 12 + 1
    return d.replace(year=y, month=m, day=min(d.day, monthrange(y, m)[1]))


@login_required
def exchange_rate_api(request):
    from django.http import JsonResponse
    from datetime import date as date_type
    frm = request.GET.get('from', '').upper()
    to  = request.GET.get('to', '').upper()
    date_str = request.GET.get('date', '')
    try:
        d = date_type.fromisoformat(date_str) if date_str else date_type.today()
    except ValueError:
        d = date_type.today()
    rate = get_price(frm, to, d)
    if rate is None:
        return JsonResponse({'rate': None, 'error': f'No price found for {frm}/{to}'})
    return JsonResponse({'rate': str(rate), 'from': frm, 'to': to, 'date': str(d)})


# ---------------------------------------------------------------------------
# Account create / edit
# ---------------------------------------------------------------------------

def _account_form_ctx(all_accounts, instance=None, post=None):
    """Build template context for the account form.

    `initial` is a flat dict of field values — always safe to access in templates
    regardless of whether we're creating or editing.
    """
    if post:
        initial = {
            'name': post.get('name', ''),
            'code': post.get('code', ''),
            'account_type': post.get('account_type', ''),
            'parent_id': post.get('parent', ''),
            'description': post.get('description', ''),
            'commodity_namespace': post.get('commodity_namespace', Account.NAMESPACE_CURRENCY),
            'commodity_mnemonic': post.get('commodity_mnemonic', 'EUR'),
            'smallest_fraction': post.get('smallest_fraction', '100'),
            'hidden': post.get('hidden') == '1',
            'placeholder': post.get('placeholder') == '1',
            'notes': post.get('notes', ''),
        }
    elif instance:
        initial = {
            'name': instance.name,
            'code': instance.code,
            'account_type': instance.account_type,
            'parent_id': str(instance.parent_id) if instance.parent_id else '',
            'description': instance.description,
            'commodity_namespace': instance.commodity_namespace,
            'commodity_mnemonic': instance.commodity_mnemonic,
            'smallest_fraction': str(instance.smallest_fraction),
            'hidden': instance.hidden,
            'placeholder': instance.placeholder,
            'notes': instance.notes,
        }
    else:
        initial = {
            'name': '', 'code': '', 'account_type': '', 'parent_id': '',
            'description': '',
            'commodity_namespace': Account.NAMESPACE_CURRENCY,
            'commodity_mnemonic': 'EUR',
            'smallest_fraction': '100',
            'hidden': False, 'placeholder': False, 'notes': '',
        }
    return {
        'all_accounts': all_accounts,
        'type_choices': Account.TYPE_CHOICES,
        'namespace_choices': Account.NAMESPACE_CHOICES,
        'fraction_choices': Account.FRACTION_CHOICES,
        'currencies': CURRENCIES,
        'account': instance,
        'initial': initial,
    }


@login_required
def account_new(request):
    all_accounts = list(Account.objects.order_by('account_type', 'name'))
    errors = []

    if request.method == 'POST':
        errors = _save_account(request, None)
        if not errors:
            messages.success(request, 'Account created.')
            return redirect('accounts_overview')

    ctx = _account_form_ctx(all_accounts, post=request.POST if errors else None)
    ctx['errors'] = errors
    return render(request, 'ledger/account_form.html', ctx)


@login_required
def account_edit(request, account_id):
    acct = get_object_or_404(Account, pk=account_id)
    all_accounts = list(Account.objects.order_by('account_type', 'name'))
    errors = []

    if request.method == 'POST':
        errors = _save_account(request, acct)
        if not errors:
            messages.success(request, 'Account saved.')
            return redirect('accounts_overview')

    ctx = _account_form_ctx(all_accounts, instance=acct, post=request.POST if errors else None)
    ctx['errors'] = errors
    return render(request, 'ledger/account_form.html', ctx)


def _save_account(request, acct):
    errors = []
    name = request.POST.get('name', '').strip()
    if not name:
        errors.append('Account name is required.')
    parent_id = request.POST.get('parent', '').strip()
    parent = None
    if parent_id:
        try:
            parent = Account.objects.get(pk=parent_id)
            if acct and parent.pk == acct.pk:
                errors.append('An account cannot be its own parent.')
        except Account.DoesNotExist:
            errors.append('Selected parent account does not exist.')

    account_type = request.POST.get('account_type', '').strip()
    valid_types = [t[0] for t in Account.TYPE_CHOICES]
    if account_type not in valid_types:
        errors.append('Invalid account type.')

    if errors:
        return errors

    commodity_namespace = request.POST.get('commodity_namespace', Account.NAMESPACE_CURRENCY)
    commodity_mnemonic = request.POST.get('commodity_mnemonic', 'EUR').strip().upper() or 'EUR'
    try:
        smallest_fraction = int(request.POST.get('smallest_fraction', '100'))
        if smallest_fraction not in [1, 10, 100, 1000]:
            smallest_fraction = 100
    except ValueError:
        smallest_fraction = 100

    if acct is None:
        acct = Account()
    acct.name = name
    acct.code = request.POST.get('code', '').strip()
    acct.account_type = account_type
    acct.parent = parent
    acct.commodity_namespace = commodity_namespace
    acct.commodity_mnemonic = commodity_mnemonic
    acct.smallest_fraction = smallest_fraction
    acct.description = request.POST.get('description', '').strip()
    acct.notes = request.POST.get('notes', '').strip()
    acct.hidden = request.POST.get('hidden') == '1'
    acct.placeholder = request.POST.get('placeholder') == '1'
    acct.save()
    return []


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


# ---------------------------------------------------------------------------
# Integrity report
# ---------------------------------------------------------------------------

@login_required
def integrity_report(request):
    from datetime import date, timedelta
    from django.db.models import Count, Q
    from django.db.models.functions import Coalesce

    today = date.today()
    issues = []

    # ── 1. Unbalanced transactions ──────────────────────────────────────────
    all_txns = Transaction.objects.prefetch_related('splits').all()
    unbalanced = []
    for txn in all_txns:
        total = sum(s.value_num for s in txn.splits.all())
        if abs(total) > Decimal('0.005'):
            unbalanced.append({'txn': txn, 'detail': f'sum = {total:+.4f}'})
    if unbalanced:
        issues.append({
            'severity': 'error',
            'title': 'Unbalanced transactions',
            'description': 'Debits do not equal credits — double-entry integrity is broken.',
            'items': [
                {'label': f'{i["txn"].post_date}  {i["txn"].description or "(no description)"}',
                 'detail': i['detail'],
                 'url': f'/transaction/{i["txn"].pk}/edit/'}
                for i in unbalanced
            ],
        })

    # ── 2. Transactions with fewer than 2 splits ───────────────────────────
    lonely = (Transaction.objects
              .annotate(sc=Count('splits'))
              .filter(sc__lt=2)
              .order_by('post_date'))
    if lonely.exists():
        issues.append({
            'severity': 'error',
            'title': 'Transactions with fewer than 2 splits',
            'description': 'Every transaction needs at least two splits for double-entry.',
            'items': [
                {'label': f'{t.post_date}  {t.description or "(no description)"}',
                 'detail': f'{t.splits.count()} split(s)',
                 'url': f'/transaction/{t.pk}/edit/'}
                for t in lonely
            ],
        })

    # ── 3. Splits with zero value ──────────────────────────────────────────
    zero_splits = (Split.objects
                   .filter(value_num=0)
                   .select_related('transaction', 'account')
                   .order_by('transaction__post_date'))
    if zero_splits.exists():
        issues.append({
            'severity': 'warning',
            'title': 'Zero-value splits',
            'description': 'Splits with amount = 0 are usually data entry errors.',
            'items': [
                {'label': f'{s.transaction.post_date}  {s.transaction.description or "(no description)"}',
                 'detail': f'account: {s.account.name}',
                 'url': f'/transaction/{s.transaction.pk}/edit/'}
                for s in zero_splits
            ],
        })

    # ── 4. Dates far in the past (before 1990) ─────────────────────────────
    ancient_cutoff = date(1990, 1, 1)
    ancient = (Transaction.objects
               .filter(post_date__lt=ancient_cutoff)
               .order_by('post_date'))
    if ancient.exists():
        issues.append({
            'severity': 'warning',
            'title': 'Transactions dated before 1990',
            'description': 'Unusually old dates may indicate import errors.',
            'items': [
                {'label': f'{t.post_date}  {t.description or "(no description)"}',
                 'detail': '',
                 'url': f'/transaction/{t.pk}/edit/'}
                for t in ancient
            ],
        })

    # ── 5. Future dates ────────────────────────────────────────────────────
    future = (Transaction.objects
              .filter(post_date__gt=today)
              .order_by('post_date'))
    if future.exists():
        issues.append({
            'severity': 'warning',
            'title': 'Transactions dated in the future',
            'description': 'Post dates beyond today may be typos.',
            'items': [
                {'label': f'{t.post_date}  {t.description or "(no description)"}',
                 'detail': f'{(t.post_date - today).days} day(s) ahead',
                 'url': f'/transaction/{t.pk}/edit/'}
                for t in future
            ],
        })

    # ── 6. Missing description ─────────────────────────────────────────────
    no_desc = (Transaction.objects
               .filter(Q(description='') | Q(description__isnull=True))
               .order_by('post_date'))
    if no_desc.exists():
        issues.append({
            'severity': 'info',
            'title': 'Transactions without a description',
            'description': 'Empty descriptions make it harder to identify transactions later.',
            'items': [
                {'label': f'{t.post_date}  (no description)',
                 'detail': f'{t.splits.count()} split(s)',
                 'url': f'/transaction/{t.pk}/edit/'}
                for t in no_desc
            ],
        })

    # ── 7. Splits on placeholder accounts ─────────────────────────────────
    placeholder_splits = (Split.objects
                          .filter(account__placeholder=True)
                          .select_related('transaction', 'account')
                          .order_by('transaction__post_date'))
    if placeholder_splits.exists():
        issues.append({
            'severity': 'error',
            'title': 'Splits assigned to placeholder accounts',
            'description': 'Placeholder accounts are category headers and should not hold transactions.',
            'items': [
                {'label': f'{s.transaction.post_date}  {s.transaction.description or "(no description)"}',
                 'detail': f'account: {s.account.name}',
                 'url': f'/transaction/{s.transaction.pk}/edit/'}
                for s in placeholder_splits
            ],
        })

    # ── 8. Splits on hidden accounts ───────────────────────────────────────
    hidden_splits = (Split.objects
                     .filter(account__hidden=True)
                     .select_related('transaction', 'account')
                     .order_by('transaction__post_date'))
    if hidden_splits.exists():
        issues.append({
            'severity': 'info',
            'title': 'Splits assigned to hidden accounts',
            'description': 'Hidden accounts are excluded from the sidebar; verify these are intentional.',
            'items': [
                {'label': f'{s.transaction.post_date}  {s.transaction.description or "(no description)"}',
                 'detail': f'account: {s.account.name}',
                 'url': f'/transaction/{s.transaction.pk}/edit/'}
                for s in hidden_splits
            ],
        })

    # ── 9. Suspense / Imbalance accounts have splits ──────────────────────
    suspense_splits = (Split.objects
                       .filter(account__name__icontains='imbalance')
                       .select_related('transaction', 'account')
                       .order_by('transaction__post_date'))
    if suspense_splits.exists():
        issues.append({
            'severity': 'error',
            'title': 'Transactions routed through Imbalance account',
            'description': 'GnuCash uses an Imbalance account when a transaction could not be balanced on import. These need a real counterpart account.',
            'items': [
                {'label': f'{s.transaction.post_date}  {s.transaction.description or "(no description)"}',
                 'detail': f'account: {s.account.name}  amount: {s.value_num:+.2f}',
                 'url': f'/transaction/{s.transaction.pk}/edit/'}
                for s in suspense_splits
            ],
        })

    # ── 10. Possible duplicate transactions ────────────────────────────────
    from collections import defaultdict
    seen = defaultdict(list)
    for txn in Transaction.objects.prefetch_related('splits').order_by('post_date'):
        key = (txn.post_date, (txn.description or '').strip().lower(),
               abs(sum(s.value_num for s in txn.splits.all() if s.value_num > 0)))
        seen[key].append(txn)
    duplicates = [group for group in seen.values() if len(group) > 1]
    if duplicates:
        items = []
        for group in duplicates:
            for txn in group:
                items.append({
                    'label': f'{txn.post_date}  {txn.description or "(no description)"}',
                    'detail': f'id {txn.pk} — same date, description and amount as {len(group)-1} other(s)',
                    'url': f'/transaction/{txn.pk}/edit/',
                })
        issues.append({
            'severity': 'warning',
            'title': 'Possible duplicate transactions',
            'description': 'Transactions sharing the same date, description and gross amount.',
            'items': items,
        })

    account_tree = _build_tree()
    return render(request, 'ledger/integrity_report.html', {
        'account_tree': account_tree,
        'issues': issues,
        'total_txns': Transaction.objects.count(),
        'total_splits': Split.objects.count(),
    })
