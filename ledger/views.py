from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import Sum
from django.contrib import messages

from .models import Account, Transaction, Split
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
# Helpers
# ---------------------------------------------------------------------------

def _build_tree():
    """Return top-level accounts with nested children attached as .child_list."""
    all_accounts = list(Account.objects.all())
    by_id = {a.pk: a for a in all_accounts}
    for a in all_accounts:
        a.child_list = []
    roots = []
    for a in all_accounts:
        if a.parent_id and a.parent_id in by_id:
            by_id[a.parent_id].child_list.append(a)
        elif a.parent_id is None:
            roots.append(a)
    return roots
