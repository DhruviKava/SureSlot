from django.urls import path, register_converter
from . import views
from core.converters import ObfuscatedIDConverter

register_converter(ObfuscatedIDConverter, 'obf_id')

urlpatterns = [
    path('dashboard/', views.tenant_dashboard, name='tenant-dashboard'),
    path('book/<obf_id:tenant_id>/', views.booking_page, name='booking-page'),
    path('book/<obf_id:tenant_id>/slots/', views.available_slots, name='available-slots'),
    path('book/<obf_id:tenant_id>/submit/', views.create_booking, name='create-booking'),
    path('book/<obf_id:tenant_id>/confirmation/<obf_id:booking_id>/', views.booking_confirmation, name='booking-confirmation'),
    path('book/<obf_id:tenant_id>/pay/', views.booking_pay, name='booking-pay'),
    path('book/<obf_id:tenant_id>/pay/verify/', views.booking_pay_verify, name='booking-pay-verify'),
    path('book/<obf_id:tenant_id>/payment-cancelled/', views.booking_payment_cancelled, name='booking-payment-cancelled'),
    path('booking/<obf_id:booking_id>/status/', views.update_booking_status, name='update-booking-status'),
]