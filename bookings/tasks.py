"""
Celery tasks for Booking Core.

Two tasks per booking are scheduled at creation time:

1. send_booking_reminder  — fires REMINDER_LEAD_MINUTES (default 24h)
   before the appointment. Sends a plain confirmation/reminder email to
   the customer. Updates reminder_sent_count and reminder_channels_used
   on the Booking row so the dashboard can show "1 reminder sent".

2. send_high_risk_nudge   — fires EXTRA_NUDGE_LEAD_MINUTES (default 2h)
   before the appointment, BUT ONLY if the booking's current risk score
   is 'high' when the task runs. This is the "extra nudge" feature from
   the original project spec: high-risk bookings automatically get an
   additional, closer-to-appointment reminder, separate from the standard
   one. Updates extra_nudge_sent=True when it fires.

Both tasks are no-ops if the booking is no longer 'scheduled' when they
fire (customer cancelled, owner marked it, etc.) — so they're safe to
schedule eagerly at booking-creation time without worrying about
phantom emails for cancelled appointments.

Both tasks log outcomes so you can inspect what actually ran:
    celery -A bookingcore worker -l info
will show each task as it fires in the worker terminal.
"""
import logging

from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # retry after 1 minute if something transient fails
    name='bookings.send_booking_reminder',
)
def send_booking_reminder(self, booking_id: int) -> str:
    """Send a standard 24-hour reminder email to the customer.

    Scheduled at booking-creation time to run REMINDER_LEAD_MINUTES
    before the appointment. Safe to run even if the booking was later
    cancelled — it checks status first and exits without sending.
    """
    # Import inside the task — avoids circular imports and ensures the
    # model is loaded in the worker's app context, not the scheduler's.
    from bookings.models import Booking

    try:
        booking = (
            Booking.objects
            .select_related('customer', 'service', 'tenant')
            .get(pk=booking_id)
        )
    except Booking.DoesNotExist:
        logger.warning("send_booking_reminder: booking %s not found, skipping.", booking_id)
        return f"booking {booking_id} not found"

    if booking.status != Booking.Status.SCHEDULED:
        logger.info(
            "send_booking_reminder: booking %s is %s, not scheduled — skipping.",
            booking_id, booking.status,
        )
        return f"skipped (status={booking.status})"

    if not booking.customer.email:
        logger.info(
            "send_booking_reminder: booking %s customer has no email — skipping.",
            booking_id,
        )
        return "skipped (no customer email)"

    try:
        subject = f"Reminder: your {booking.service.name} appointment at {booking.tenant.name}"
        body = render_to_string('bookings/emails/reminder.txt', {
            'booking': booking,
            'local_dt': timezone.localtime(booking.appointment_datetime),
        })
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[booking.customer.email],
            fail_silently=False,
        )

        # Record that a reminder was sent — these fields are used by the
        # ML model as features and shown on the dashboard.
        booking.reminder_sent_count += 1
        if booking.reminder_channels_used:
            booking.reminder_channels_used += ',email'
        else:
            booking.reminder_channels_used = 'email'
        booking.save(update_fields=['reminder_sent_count', 'reminder_channels_used'])

        logger.info("send_booking_reminder: sent reminder for booking %s to %s",
                    booking_id, booking.customer.email)
        return f"sent to {booking.customer.email}"

    except Exception as exc:
        logger.error("send_booking_reminder: error for booking %s: %s", booking_id, exc)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    name='bookings.send_high_risk_nudge',
)
def send_high_risk_nudge(self, booking_id: int) -> str:
    """Send an extra nudge email for high-risk bookings, 2 hours before.

    This is the feature that closes the loop between the ML risk score
    and a real action: if the model still thinks this booking is high-risk
    when this task fires (2 hours before the appointment), we send an
    additional, more urgent reminder — "We're expecting you in 2 hours."

    The risk is re-evaluated at task-run time, not at schedule time,
    because a customer might pay the deposit between scheduling and firing,
    which would lower the risk and make the nudge unnecessary.
    """
    from bookings.models import Booking
    from bookings.risk import score_booking, risk_bucket

    try:
        booking = (
            Booking.objects
            .select_related('customer', 'service', 'tenant')
            .get(pk=booking_id)
        )
    except Booking.DoesNotExist:
        logger.warning("send_high_risk_nudge: booking %s not found, skipping.", booking_id)
        return f"booking {booking_id} not found"

    if booking.status != Booking.Status.SCHEDULED:
        logger.info(
            "send_high_risk_nudge: booking %s is %s — skipping.",
            booking_id, booking.status,
        )
        return f"skipped (status={booking.status})"

    if booking.extra_nudge_sent:
        logger.info("send_high_risk_nudge: booking %s already sent nudge — skipping.", booking_id)
        return "skipped (already sent)"

    if not booking.customer.email:
        logger.info("send_high_risk_nudge: booking %s customer has no email — skipping.", booking_id)
        return "skipped (no customer email)"

    # Re-evaluate risk now — not at schedule time.
    current_score = score_booking(booking)
    current_bucket = risk_bucket(current_score)

    if current_bucket != 'high':
        logger.info(
            "send_high_risk_nudge: booking %s risk is now %s (score %.2f) — no nudge needed.",
            booking_id, current_bucket, current_score,
        )
        return f"skipped (risk now={current_bucket})"

    try:
        subject = f"See you in 2 hours — {booking.service.name} at {booking.tenant.name}"
        body = render_to_string('bookings/emails/high_risk_nudge.txt', {
            'booking': booking,
            'local_dt': timezone.localtime(booking.appointment_datetime),
            'risk_score_pct': int(current_score * 100),
        })
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[booking.customer.email],
            fail_silently=False,
        )

        booking.extra_nudge_sent = True
        booking.reminder_sent_count += 1
        if booking.reminder_channels_used:
            booking.reminder_channels_used += ',email-nudge'
        else:
            booking.reminder_channels_used = 'email-nudge'
        booking.save(update_fields=['extra_nudge_sent', 'reminder_sent_count', 'reminder_channels_used'])

        logger.info(
            "send_high_risk_nudge: sent extra nudge for booking %s (score=%.2f) to %s",
            booking_id, current_score, booking.customer.email,
        )
        return f"nudge sent to {booking.customer.email} (score={current_score:.2f})"

    except Exception as exc:
        logger.error("send_high_risk_nudge: error for booking %s: %s", booking_id, exc)
        raise self.retry(exc=exc)


@shared_task(name='bookings.tasks.process_upcoming_reminders')
def process_upcoming_reminders() -> str:
    """Scan scheduled bookings in the next 24 hours and trigger reminders.

    Runs hourly via Celery Beat schedule.
    - Triggers send_booking_reminder for bookings within 24 hours that haven't had one.
    - Triggers send_high_risk_nudge for bookings within 2 hours that haven't had one.
    """
    from bookings.models import Booking
    from django.utils import timezone
    from datetime import timedelta

    now = timezone.now()

    # 1. Process Standard Reminders (bookings in the next 24 hours)
    upcoming_standard = Booking.objects.filter(
        status=Booking.Status.SCHEDULED,
        appointment_datetime__gt=now,
        appointment_datetime__lte=now + timedelta(hours=24),
        reminder_sent_count=0,
    )
    standard_count = 0
    for booking in upcoming_standard:
        send_booking_reminder.delay(booking.id)
        standard_count += 1

    # 2. Process High-Risk Nudges (bookings in the next 2 hours)
    upcoming_nudges = Booking.objects.filter(
        status=Booking.Status.SCHEDULED,
        appointment_datetime__gt=now,
        appointment_datetime__lte=now + timedelta(hours=2),
        extra_nudge_sent=False,
    )
    nudge_count = 0
    for booking in upcoming_nudges:
        send_high_risk_nudge.delay(booking.id)
        nudge_count += 1

    return f"Triggered {standard_count} standard reminders and {nudge_count} high-risk nudges."