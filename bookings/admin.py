from django.contrib import admin
from .models import Booking


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = (
        "customer",
        "service",
        "staff",
        "tenant",
        "appointment_datetime",
        "lead_time_hours",
        "deposit_paid",
        "status",
        "predicted_risk_score",
    )
    list_filter = ("tenant", "status", "booking_channel", "deposit_paid")
    search_fields = ("customer__name", "customer__phone")
    readonly_fields = ("lead_time_hours", "created_at")
    date_hierarchy = "appointment_datetime"

