from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('account/<int:account_id>/', views.account_register, name='account_register'),
    path('import/', views.import_gnucash, name='import_gnucash'),
    path('preferences/', views.preferences, name='preferences'),
    path('transaction/<int:txn_id>/edit/', views.transaction_edit, name='transaction_edit'),
    path('account/<int:account_id>/new/', views.transaction_new, name='transaction_new'),
    path('account/<int:account_id>/autocomplete/', views.transaction_autocomplete, name='transaction_autocomplete'),
    path('accounts/', views.accounts_overview, name='accounts_overview'),
    path('prices/', views.price_list, name='price_list'),
    path('prices/update/', views.update_prices_ajax, name='update_prices_ajax'),
    path('api/exchange-rate/', views.exchange_rate_api, name='exchange_rate_api'),
    path('accounts/new/', views.account_new, name='account_new'),
    path('accounts/<int:account_id>/edit/', views.account_edit, name='account_edit'),
    path('reports/networth/', views.networth_report, name='networth_report'),
    path('reports/networth/data/', views.networth_report_data, name='networth_report_data'),
    path('reports/saved/', views.saved_reports, name='saved_reports'),
]
