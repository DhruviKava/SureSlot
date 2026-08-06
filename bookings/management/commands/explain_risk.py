"""
Management command: explain exactly how the rule-based stub's score was
calculated, factor by factor — a permanent, reusable debugging tool.

Note: as of Step 5, score_booking() (the real entry point the app uses)
calls the trained model, not this stub. This command explains the STUB's
math specifically — useful for understanding the original heuristic — and
also prints the real model's score alongside it so the two are never
silently out of sync without you knowing. For a multi-booking side-by-side
comparison, see the `compare_risk` command instead.

Usage:
    python manage.py explain_risk 22 23
    python manage.py explain_risk 22          # single booking is fine too
"""

from django.core.management.base import BaseCommand, CommandError

from bookings.models import Booking
from bookings.risk import (
    score_booking,
    score_booking_stub,
    risk_bucket,
    model_is_available,
    _lead_time_factor,
    _deposit_factor,
    _channel_factor,
    _history_factor,
    LEAD_TIME_WEIGHT,
    DEPOSIT_WEIGHT,
    CHANNEL_WEIGHT,
    HISTORY_WEIGHT,
)


class Command(BaseCommand):
    help = "Print the rule-based stub's factor breakdown for one or more bookings, alongside the real model's score."

    def add_arguments(self, parser):
        parser.add_argument(
            'booking_ids',
            nargs='+',
            type=int,
            help='One or more Booking primary keys to inspect.',
        )

    def handle(self, *args, **options):
        if not model_is_available():
            self.stdout.write(self.style.WARNING(
                "No trained model loaded — score_booking() is currently running in stub "
                "fallback mode, so 'real model score' below will match the stub exactly.\n"
            ))

        for booking_id in options['booking_ids']:
            try:
                booking = Booking.objects.select_related('customer', 'service', 'tenant').get(pk=booking_id)
            except Booking.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"No booking with id={booking_id} found."))
                continue

            lead_factor = _lead_time_factor(booking.lead_time_hours)
            deposit_factor = _deposit_factor(booking.deposit_required, booking.deposit_paid)
            channel_factor = _channel_factor(booking.booking_channel)
            history_factor = _history_factor(booking.customer, booking.tenant_id, exclude_booking_id=booking.id)

            stub_score = score_booking_stub(booking)
            real_score = score_booking(booking)  # may be model-driven or stub fallback

            self.stdout.write(self.style.MIGRATE_HEADING(
                f"\n--- Booking {booking.id}: {booking.customer.name} / {booking.service.name} "
                f"({booking.tenant.name}) ---"
            ))
            self.stdout.write(f"  status:            {booking.status}")
            self.stdout.write(self.style.MIGRATE_LABEL("  --- rule-based stub breakdown ---"))
            self.stdout.write(
                f"  lead_time_hours:   {booking.lead_time_hours} "
                f"-> factor {lead_factor:.2f} x weight {LEAD_TIME_WEIGHT} = {lead_factor * LEAD_TIME_WEIGHT:.3f}"
            )
            self.stdout.write(
                f"  deposit (req/paid): {booking.deposit_required}/{booking.deposit_paid} "
                f"-> factor {deposit_factor:.2f} x weight {DEPOSIT_WEIGHT} = {deposit_factor * DEPOSIT_WEIGHT:.3f}"
            )
            self.stdout.write(
                f"  channel:           {booking.booking_channel} "
                f"-> factor {channel_factor:.2f} x weight {CHANNEL_WEIGHT} = {channel_factor * CHANNEL_WEIGHT:.3f}"
            )
            self.stdout.write(
                f"  customer history:  -> factor {history_factor:.2f} x weight {HISTORY_WEIGHT} = {history_factor * HISTORY_WEIGHT:.3f}"
            )
            self.stdout.write(f"  STUB SCORE: {stub_score} -> bucket: {risk_bucket(stub_score)}")
            self.stdout.write(self.style.SUCCESS(
                f"\n  REAL SCORE (what the app actually uses): {real_score} -> bucket: {risk_bucket(real_score)}"
            ))