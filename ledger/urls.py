from django.urls import path
from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('account/<int:account_id>/', views.account_register, name='account_register'),
    path('import/', views.import_gnucash, name='import_gnucash'),
]
