from django.db import models
from django.core.exceptions import ValidationError

from tenants.models import Tenant, StaffMember, Service, Customer


class Booking(models.Model):
    """A single appointment booking.

    This is the row that becomes one training example for the no-show
    classifier in Phase 3. Every field here exists because it plausibly
    affects whether the customer shows up — this table doubles as the
    feature store for the prediction model, which is why it's richer
    than a typical CRUD booking record.
    """

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Scheduled"
        COMPLETED = "completed", "Completed"
        NO_SHOW = "no_show", "No-show"
        CANCELLED = "cancelled", "Cancelled"
        RESCHEDULED = "rescheduled", "Rescheduled"

    class BookingChannel(models.TextChoices):
        ONLINE = "online", "Online"
        PHONE = "phone", "Phone"
        WALK_IN = "walk_in", "Walk-in"
        APP = "app", "App"

    tenant = models.ForeignKey(
        Tenant, on_delete=models.CASCADE, related_name="bookings"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, related_name="bookings"
    )
    staff = models.ForeignKey(
        StaffMember, on_delete=models.SET_NULL, null=True, related_name="bookings"
    )
    service = models.ForeignKey(
        Service, on_delete=models.SET_NULL, null=True, related_name="bookings"
    )

    # --- Timing ---
    created_at = models.DateTimeField(
        auto_now_add=True, help_text="When the booking was MADE."
    )
    appointment_datetime = models.DateTimeField(
        help_text="When the appointment is SCHEDULED for."
    )
    lead_time_hours = models.FloatField(
        editable=False,
        null=True,
        help_text="Hours between booking creation and the appointment. "
        "The single strongest predictor candidate — stored directly "
        "rather than recomputed, since it's used heavily downstream.",
    )

    # --- Channel & commitment signals ---
    booking_channel = models.CharField(
        max_length=20, choices=BookingChannel.choices, default=BookingChannel.ONLINE
    )
    deposit_required = models.BooleanField(
        help_text="Snapshot of service.requires_deposit at booking time. "
        "Not a live lookup — historical bookings must not silently change "
        "meaning if a tenant edits their service config later."
    )
    deposit_paid = models.BooleanField(default=False)

    # --- Reminder / nudge automation (feeds back from the Celery layer) ---
    reminder_sent_count = models.PositiveSmallIntegerField(default=0)
    reminder_channels_used = models.CharField(
        max_length=100,
        blank=True,
        help_text="Comma-separated, e.g. 'sms,email'.",
    )
    extra_nudge_sent = models.BooleanField(
        default=False,
        help_text="True if this booking was flagged high-risk and received "
        "an additional automated reminder beyond the standard one. Lets us "
        "later compare outcomes for nudged vs non-nudged high-risk bookings.",
    )

    # --- Outcome & prediction ---
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SCHEDULED
    )
    predicted_risk_score = models.FloatField(
        null=True,
        blank=True,
        help_text="Model's no-show probability at prediction time (0-1). "
        "Stored as a snapshot — not recomputed later — so predicted-vs-"
        "actual comparisons remain meaningful for the demo/calibration chart.",
    )

    class Meta:
        ordering = ["appointment_datetime"]
        indexes = [
            models.Index(fields=["tenant", "appointment_datetime"]),
            models.Index(fields=["customer"]),
            models.Index(fields=["status"]),
        ]

    def clean(self):
        if self.customer_id and self.tenant_id and self.customer.tenant_id != self.tenant_id:
            raise ValidationError("Customer does not belong to this tenant.")
        if self.staff_id and self.tenant_id and self.staff.tenant_id != self.tenant_id:
            raise ValidationError("Staff member does not belong to this tenant.")
        if self.service_id and self.tenant_id and self.service.tenant_id != self.tenant_id:
            raise ValidationError("Service does not belong to this tenant.")

    def save(self, *args, **kwargs):
        # Snapshot deposit_required from the service at creation time only.
        if self._state.adding and self.service_id and self.deposit_required is None:
            self.deposit_required = self.service.requires_deposit

        # Derive lead_time_hours from created_at / appointment_datetime.
        # On first save created_at isn't set yet (auto_now_add fills it
        # during the INSERT), so we approximate with timezone.now() — close
        # enough for a feature that's measured in hours, not seconds.
        from django.utils import timezone

        reference_created = self.created_at or timezone.now()
        if self.appointment_datetime:
            delta = self.appointment_datetime - reference_created
            self.lead_time_hours = round(delta.total_seconds() / 3600, 2)

        super().save(*args, **kwargs)

    @property
    def encoded_id(self):
        from core.obfuscator import encode_id
        return encode_id(self.pk)

    def __str__(self):
        return (
            f"{self.customer.name} -> {self.service} "
            f"@ {self.appointment_datetime:%Y-%m-%d %H:%M} ({self.status})"
        )

