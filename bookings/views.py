import calendar
import json
import hashlib
import hmac
import razorpay

from datetime import date, datetime, timedelta
from collections import defaultdict

from django.shortcuts import render, get_object_or_404, redirect
from django.http import Http404, JsonResponse, HttpResponseBadRequest
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.core.exceptions import ValidationError

from django.conf import settings
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from tenants.models import Tenant, Service, StaffMember, Customer
from .models import Booking
from .risk import score_booking, risk_bucket


@login_required(login_url='login')
def tenant_dashboard(request):
    """Month-view calendar dashboard for the LOGGED-IN user's own tenant.

    There is no tenant_id in the URL anymore — the tenant is determined by
    who is logged in (request.user.tenant), not by what number happens to
    be typed into the address bar. This is the actual data-isolation
    enforcement point: previously /dashboard/<id>/ was open to anyone who
    guessed an ID, regardless of which business they were supposed to see.

    Superusers with no tenant attached (e.g. the platform admin account)
    get a clear message rather than a crash, since there's no business
    data to show them here — they manage things via /admin/ instead.
    """
    tenant = request.user.tenant
    if tenant is None:
        return render(request, 'bookings/no_tenant.html', {})

    is_owner = request.user.role == 'owner'

    today = timezone.localdate()
    try:
        year = int(request.GET.get('year', today.year))
        month = int(request.GET.get('month', today.month))
        if not (1 <= month <= 12):
            raise ValueError
    except (TypeError, ValueError):
        raise Http404("Invalid month/year.")

    # Bookings for this tenant, in this month, with related objects
    # pre-fetched in one query (avoids N+1 lookups when the template
    # reads booking.customer.name / booking.service.name per chip).
    month_bookings = (
        Booking.objects.filter(
            tenant=tenant,
            appointment_datetime__year=year,
            appointment_datetime__month=month,
        )
        .select_related('customer', 'service', 'staff')
        .order_by('appointment_datetime')
    )

    bookings_by_day = defaultdict(list)
    high_risk_bookings = []
    for booking in month_bookings:
        booking.is_past = booking.appointment_datetime <= timezone.now()
        if booking.status == Booking.Status.SCHEDULED:
            booking.risk_score = score_booking(booking)
            booking.risk_label = risk_bucket(booking.risk_score)
            if booking.risk_label == 'high' and booking.appointment_datetime >= timezone.now():
                high_risk_bookings.append(booking)
        else:
            booking.risk_score = None
            booking.risk_label = None
        local_dt = timezone.localtime(booking.appointment_datetime)
        bookings_by_day[local_dt.day].append(booking)

    # Smart scheduling suggestions: the riskiest upcoming slots, ranked by
    # score. This is the "consider double-booking" feature — surfacing the
    # specific bookings most likely to no-show as an actionable list, not
    # just a passive badge buried in the calendar grid. Capped at 5 so this
    # stays a quick morning glance, not another wall of data.
    overbooking_suggestions = sorted(
        high_risk_bookings, key=lambda b: b.risk_score, reverse=True
    )[:5]

    # calendar.Calendar gives us full weeks including the leading/trailing
    # days from adjacent months, so the grid always renders as complete
    # 7-day rows rather than a ragged first/last week.
    cal = calendar.Calendar(firstweekday=0)  # Monday-first
    weeks = []
    for week in cal.monthdatescalendar(year, month):
        week_cells = []
        for day_date in week:
            is_current_month = day_date.month == month
            week_cells.append({
                'date': day_date,
                'is_current_month': is_current_month,
                'is_today': day_date == today,
                'bookings': bookings_by_day.get(day_date.day, []) if is_current_month else [],
            })
        weeks.append(week_cells)

    # Previous/next month for the nav arrows, handling year rollover.
    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)

    upcoming_qs = [b for week in weeks for cell in week for b in cell['bookings']] if False else None
    high_risk_count = sum(
        1 for b in month_bookings
        if b.status == Booking.Status.SCHEDULED and getattr(b, 'risk_label', None) == 'high'
    )
    no_show_count = month_bookings.filter(status=Booking.Status.NO_SHOW).count() if hasattr(Booking.Status, 'NO_SHOW') else sum(
        1 for b in month_bookings if b.status == 'no_show'
    )
    scheduled_count = sum(1 for b in month_bookings if b.status == Booking.Status.SCHEDULED)

    context = {
        'tenant': tenant,
        'is_owner': is_owner,
        'weeks': weeks,
        'overbooking_suggestions': overbooking_suggestions,
        'high_risk_count': high_risk_count,
        'no_show_count': no_show_count,
        'scheduled_count': scheduled_count,
        'month_name': calendar.month_name[month],
        'year': year,
        'month': month,
        'prev_month': prev_month,
        'prev_year': prev_year,
        'next_month': next_month,
        'next_year': next_year,
        'total_bookings_this_month': month_bookings.count(),
    }
    return render(request, 'bookings/dashboard.html', context)


# ---------------------------------------------------------------------------
# Public booking form
# ---------------------------------------------------------------------------

# Phase 1 simplification: no Availability table exists yet (deliberately cut
# during schema design), so business hours are a fixed assumption rather than
# per-tenant configuration. This is a known limitation, documented in the
# README, not a silently-pretended feature.
BUSINESS_OPEN_HOUR = 9
BUSINESS_CLOSE_HOUR = 18


def booking_page(request, tenant_id):
    """Public-facing booking page: pick a service, see availability, book."""
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    services = Service.objects.filter(tenant=tenant, is_active=True).order_by('name')
    staff = StaffMember.objects.filter(tenant=tenant, is_active=True).order_by('name')

    context = {
        'tenant': tenant,
        'services': services,
        'staff': staff,
        'today_iso': timezone.localdate().isoformat(),
    }
    return render(request, 'bookings/booking_form.html', context)


def _generate_day_slots(tenant_id, service_duration_minutes, day, staff_id=None):
    """Return a list of (datetime, available_staff_ids) tuples for one day.

    Slots are generated at fixed intervals matching the service duration,
    between BUSINESS_OPEN_HOUR and BUSINESS_CLOSE_HOUR. A slot is only
    included if at least one staff member (or the specifically requested
    one) has no overlapping booking at that time.
    """
    staff_qs = StaffMember.objects.filter(tenant_id=tenant_id, is_active=True)
    if staff_id:
        staff_qs = staff_qs.filter(pk=staff_id)
    staff_list = list(staff_qs)
    if not staff_list:
        return []

    # Pull existing bookings for the day once, instead of querying per-slot.
    day_start = datetime.combine(day, datetime.min.time())
    day_start = timezone.make_aware(day_start)
    day_end = day_start + timedelta(days=1)

    existing = Booking.objects.filter(
        tenant_id=tenant_id,
        appointment_datetime__gte=day_start,
        appointment_datetime__lt=day_end,
        staff_id__in=[s.id for s in staff_list],
    ).exclude(status='cancelled').values('staff_id', 'appointment_datetime', 'service__duration_minutes')

    # Build a quick lookup: staff_id -> list of (start, end) busy ranges.
    busy_ranges = defaultdict(list)
    for b in existing:
        start = b['appointment_datetime']
        duration = b['service__duration_minutes'] or 30
        end = start + timedelta(minutes=duration)
        busy_ranges[b['staff_id']].append((start, end))

    duration = timedelta(minutes=service_duration_minutes)
    slot_start = day_start.replace(hour=BUSINESS_OPEN_HOUR, minute=0)
    close_time = day_start.replace(hour=BUSINESS_CLOSE_HOUR, minute=0)

    slots = []
    now = timezone.now()
    while slot_start + duration <= close_time:
        if slot_start > now:  # don't offer slots in the past
            available_staff_ids = []
            for s in staff_list:
                conflict = any(
                    slot_start < busy_end and (slot_start + duration) > busy_start
                    for busy_start, busy_end in busy_ranges.get(s.id, [])
                )
                if not conflict:
                    available_staff_ids.append(s.id)
            if available_staff_ids:
                slots.append((slot_start, available_staff_ids))
        slot_start += duration

    return slots


@require_GET
def available_slots(request, tenant_id):
    """AJAX endpoint: given a service + date (+ optional staff), return
    available time slots as JSON. Called when the customer picks a date.
    """
    service_id = request.GET.get('service_id')
    date_str = request.GET.get('date')
    staff_id = request.GET.get('staff_id') or None

    if not service_id or not date_str:
        return HttpResponseBadRequest("service_id and date are required.")

    try:
        day = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return HttpResponseBadRequest("date must be YYYY-MM-DD.")

    service = get_object_or_404(Service, pk=service_id, tenant_id=tenant_id, is_active=True)

    slots = _generate_day_slots(tenant_id, service.duration_minutes, day, staff_id)

    return JsonResponse({
        'slots': [
            {
                'time': timezone.localtime(slot_dt).strftime('%H:%M'),
                'iso': slot_dt.isoformat(),
                'staff_ids': staff_ids,
            }
            for slot_dt, staff_ids in slots
        ]
    })


def _assign_staff(available_staff_ids, day):
    """Load-balanced auto-assignment: pick whichever available staff member
    has the fewest bookings that day. Ties broken by lowest id for
    determinism (easier to reason about in tests/demos than random).
    """
    day_start = timezone.make_aware(datetime.combine(day, datetime.min.time()))
    day_end = day_start + timedelta(days=1)

    counts = (
        Booking.objects.filter(
            staff_id__in=available_staff_ids,
            appointment_datetime__gte=day_start,
            appointment_datetime__lt=day_end,
        )
        .exclude(status='cancelled')
        .values('staff_id')
        .annotate(n=Count('id'))
    )
    count_by_staff = {c['staff_id']: c['n'] for c in counts}

    return min(available_staff_ids, key=lambda sid: (count_by_staff.get(sid, 0), sid))


def _booking_form_error(request, tenant, message):
    """Shared helper: re-render the booking form with an error, instead of
    repeating this five-field context dict in every failure branch."""
    return render(request, 'bookings/booking_form.html', {
        'tenant': tenant,
        'services': Service.objects.filter(tenant=tenant, is_active=True).order_by('name'),
        'staff': StaffMember.objects.filter(tenant=tenant, is_active=True).order_by('name'),
        'today_iso': timezone.localdate().isoformat(),
        'error': message,
    })


def _resolve_slot_and_staff(tenant_id, service, appointment_dt, staff_id):
    """Shared slot-validation logic, used by both the no-deposit path and
    the post-payment path (Stripe's success redirect re-validates the slot
    again, since several minutes may have passed during checkout)."""
    day = timezone.localtime(appointment_dt).date()
    slots = _generate_day_slots(tenant_id, service.duration_minutes, day, staff_id)
    matching_slot = next((s for s in slots if s[0] == appointment_dt), None)
    if matching_slot is None:
        for adjacent_day in (day - timedelta(days=1), day + timedelta(days=1)):
            adjacent_slots = _generate_day_slots(tenant_id, service.duration_minutes, adjacent_day, staff_id)
            matching_slot = next((s for s in adjacent_slots if s[0] == appointment_dt), None)
            if matching_slot:
                break
    return matching_slot


@require_POST
def create_booking(request, tenant_id):
    """Handle booking form submission.

    Two paths from here, depending on the chosen service:
      - No deposit required: behaves exactly as before — create the
        Booking immediately, go straight to confirmation.
      - Deposit required: do NOT create a Booking yet. Instead, create a
        Stripe Checkout Session (test mode) and redirect the customer
        there. The Booking is only created in booking_payment_success,
        after Stripe confirms the payment actually succeeded — so
        deposit_paid=True is never set without a real (test-mode)
        payment behind it, and we never leave a half-paid Booking row
        sitting in the database if the customer abandons checkout.
    """
    tenant = get_object_or_404(Tenant, pk=tenant_id)

    service_id = request.POST.get('service_id')
    slot_iso = request.POST.get('slot_iso')
    staff_id = request.POST.get('staff_id') or None
    customer_name = request.POST.get('customer_name', '').strip()
    customer_phone = request.POST.get('customer_phone', '').strip()
    customer_email = request.POST.get('customer_email', '').strip()

    if not all([service_id, slot_iso, customer_name, customer_phone]):
        return _booking_form_error(request, tenant, "Please fill in all required fields and select a time slot.")

    service = get_object_or_404(Service, pk=service_id, tenant=tenant, is_active=True)
    try:
        appointment_dt = datetime.fromisoformat(slot_iso)
    except ValueError:
        return _booking_form_error(request, tenant, "Invalid time slot. Please pick a time again.")
    if timezone.is_naive(appointment_dt):
        appointment_dt = timezone.make_aware(appointment_dt)

    matching_slot = _resolve_slot_and_staff(tenant.id, service, appointment_dt, staff_id)
    if matching_slot is None:
        return _booking_form_error(
            request, tenant,
            "That slot is no longer available — someone may have just booked it. Please pick another time."
        )
    available_staff_ids = matching_slot[1]
    assigned_staff_id = staff_id if (staff_id and int(staff_id) in available_staff_ids) \
        else _assign_staff(available_staff_ids, appointment_dt.date())

    # Find-or-create the customer by phone, scoped to this tenant.
    customer, _ = Customer.objects.get_or_create(
        tenant=tenant,
        phone=customer_phone,
        defaults={'name': customer_name, 'email': customer_email},
    )

    if not service.requires_deposit:
        # Original unchanged path — no payment involved.
        booking = Booking(
            tenant=tenant, customer=customer, staff_id=assigned_staff_id, service=service,
            appointment_datetime=appointment_dt, booking_channel=Booking.BookingChannel.ONLINE,
            status=Booking.Status.SCHEDULED, deposit_required=False,
        )
        booking.full_clean()
        booking.save()
        return redirect('booking-confirmation', tenant_id=tenant.id, booking_id=booking.id)

    # Deposit path — redirect to our own payment page which opens the
    # Razorpay modal. We carry the booking details in the session (not
    # URL params) so a customer can't tamper with the amount or service.
    deposit_amount_paise = int(
        round(float(service.price) * settings.DEPOSIT_PERCENTAGE / 100, 2) * 100
    )  # Razorpay expects the smallest currency unit — paise for INR.

    request.session['pending_booking'] = {
        'tenant_id': tenant.id,
        'service_id': service.id,
        'staff_id': assigned_staff_id,
        'appointment_iso': appointment_dt.isoformat(),
        'customer_id': customer.id,
        'deposit_amount_paise': deposit_amount_paise,
    }
    return redirect('booking-pay', tenant_id=tenant.id)

def booking_pay(request, tenant_id):
    """Creates a Razorpay order and renders the payment page with the
    checkout modal. The pending booking details come from the session —
    never from URL params, so the customer can't tamper with the amount.
    """
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    pending = request.session.get('pending_booking')

    if not pending or pending.get('tenant_id') != tenant_id:
        return redirect('booking-page', tenant_id=tenant_id)

    client = razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )
    service = get_object_or_404(Service, pk=pending['service_id'], tenant=tenant)

    order = client.order.create({
        'amount': pending['deposit_amount_paise'],
        'currency': 'INR',
        'payment_capture': 1,  # Auto-capture on payment success.
        'notes': {
            'tenant': tenant.name,
            'service': service.name,
            'deposit_pct': settings.DEPOSIT_PERCENTAGE,
        },
    })

    # Store the Razorpay order_id in the session so we can verify it
    # against the payment response — prevents a tampered order_id.
    request.session['razorpay_order_id'] = order['id']
    request.session.modified = True

    customer = get_object_or_404(Customer, pk=pending['customer_id'], tenant=tenant)

    return render(request, 'bookings/payment_page.html', {
        'tenant': tenant,
        'service': service,
        'razorpay_key_id': settings.RAZORPAY_KEY_ID,
        'razorpay_order_id': order['id'],
        'amount_paise': pending['deposit_amount_paise'],
        'amount_display': pending['deposit_amount_paise'] / 100,
        'customer_name': customer.name,
        'customer_email': customer.email or '',
        'deposit_pct': settings.DEPOSIT_PERCENTAGE,
        'verify_url': reverse('booking-pay-verify', kwargs={'tenant_id': tenant_id}),
    })


@csrf_exempt
@require_POST
def booking_pay_verify(request, tenant_id):
    """Verifies the Razorpay payment signature and creates the Booking.

    Razorpay's signature is HMAC-SHA256 of
    '{order_id}|{payment_id}' using our key_secret. If it matches,
    the payment is genuine and we create the Booking with deposit_paid=True.
    If it doesn't match, someone tampered with the response — reject it.
    """
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    pending = request.session.get('pending_booking')
    expected_order_id = request.session.get('razorpay_order_id')

    payment_id = request.POST.get('razorpay_payment_id', '')
    order_id = request.POST.get('razorpay_order_id', '')
    signature = request.POST.get('razorpay_signature', '')

    # Verify the order_id matches what we created — not what the client
    # claims. Prevents substitution of a cheaper order's payment.
    if not pending or order_id != expected_order_id:
        return render(request, 'bookings/payment_cancelled.html', {
            'tenant': tenant, 'error': 'Session mismatch.'
        })

    # Cryptographic signature check — this is the actual security layer.
    message = f"{order_id}|{payment_id}".encode()
    expected_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode(), message, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, signature):
        return render(request, 'bookings/payment_cancelled.html', {
            'tenant': tenant, 'error': 'Payment verification failed.'
        })

    # Signature is valid — payment is genuine. Now create the Booking.
    service = get_object_or_404(Service, pk=pending['service_id'], tenant=tenant)
    customer = get_object_or_404(Customer, pk=pending['customer_id'], tenant=tenant)
    appointment_dt = datetime.fromisoformat(pending['appointment_iso'])
    staff_id = pending.get('staff_id')

    # Re-validate slot (time has passed during checkout).
    matching_slot = _resolve_slot_and_staff(tenant.id, service, appointment_dt, staff_id)
    if matching_slot is None:
        return render(request, 'bookings/payment_cancelled.html', {
            'tenant': tenant, 'slot_taken': True
        })

    # Deduplicate if success fires twice (browser refresh).
    existing = Booking.objects.filter(
        tenant=tenant, customer=customer,
        service=service, appointment_datetime=appointment_dt,
    ).first()
    if existing:
        return redirect('booking-confirmation', tenant_id=tenant.id, booking_id=existing.id)

    booking = Booking(
        tenant=tenant, customer=customer, staff_id=staff_id, service=service,
        appointment_datetime=appointment_dt,
        booking_channel=Booking.BookingChannel.ONLINE,
        status=Booking.Status.SCHEDULED,
        deposit_required=True, deposit_paid=True,
    )
    booking.full_clean()
    booking.save()

    # Clean up session.
    del request.session['pending_booking']
    del request.session['razorpay_order_id']

    return redirect('booking-confirmation', tenant_id=tenant.id, booking_id=booking.id)


def booking_payment_cancelled(request, tenant_id):
    tenant = get_object_or_404(Tenant, pk=tenant_id)
    return render(request, 'bookings/payment_cancelled.html', {'tenant': tenant})


def booking_confirmation(request, tenant_id, booking_id):
    booking = get_object_or_404(
        Booking.objects.select_related('customer', 'service', 'staff', 'tenant'),
        pk=booking_id, tenant_id=tenant_id,
    )
    return render(request, 'bookings/booking_confirmation.html', {'booking': booking})


@login_required(login_url='login')
@require_POST
def update_booking_status(request, booking_id):
    """AJAX endpoint: Owner marks a booking as completed or no-show
    directly from the calendar chip, without going to /admin/.

    This closes the feedback loop for the ML model — recorded no-show
    outcomes become training data that makes future predictions more
    accurate. Only Owners can mark outcomes; Staff see their own
    bookings but cannot change historical records.

    Returns JSON so the chip updates in-place without a full page reload.
    """
    if request.user.tenant is None or request.user.role != 'owner':
        return JsonResponse({'error': 'Not permitted.'}, status=403)

    new_status = request.POST.get('status')
    if new_status not in (Booking.Status.COMPLETED, Booking.Status.NO_SHOW):
        return JsonResponse({'error': 'Invalid status.'}, status=400)

    # Scope to this owner's tenant — a guessed booking_id from another
    # tenant returns 404, not silently updating their data.
    booking = get_object_or_404(
        Booking, pk=booking_id, tenant=request.user.tenant,
        status=Booking.Status.SCHEDULED,
    )

    if booking.appointment_datetime > timezone.now():
        return JsonResponse({'error': 'Cannot update the status of a future booking.'}, status=400)

    booking.status = new_status
    booking.save(update_fields=['status'])

    return JsonResponse({
        'ok': True,
        'booking_id': booking.id,
        'new_status': new_status,
        'new_status_display': booking.get_status_display(),
    })