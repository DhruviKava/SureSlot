"""
Management command: generate a synthetic, isolated training dataset for
Step 5's no-show classifier.

Creates dedicated "Synthetic ... — Training Data" tenants, staff,
services, customers, and ~800 historical bookings spread across the past
3 months. Customers are assigned a hidden "persona" (reliable / casual /
chronic-no-show) that drives correlated, realistic behavior — but the
persona itself is NEVER stored anywhere the model could see it. The model
will only ever see the same columns as the real Booking table (lead time,
deposit, channel, customer history) — it has to discover the pattern from
observable behavior, exactly like a real model would.

This is deliberately isolated from real tenant data: running this command
never touches Bloom Hair Studio, Parul's_T2, or any other real tenant —
it only ever creates/clears its own clearly-labeled synthetic tenants.

Usage:
    python manage.py generate_training_data
    python manage.py generate_training_data --reset   # wipe and regenerate
"""

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from tenants.models import Tenant, StaffMember, Service, Customer
from bookings.models import Booking


SYNTHETIC_TENANT_PREFIX = "Synthetic"
SYNTHETIC_TENANT_SUFFIX = "— Training Data"

TENANT_BLUEPRINTS = [
    {
        "name": f"{SYNTHETIC_TENANT_PREFIX} Salon {SYNTHETIC_TENANT_SUFFIX}",
        "business_type": Tenant.BusinessType.SALON,
        "staff": ["Staff A", "Staff B"],
        "services": [
            ("Haircut", 30, 25, False),
            ("Color & Style", 90, 90, True),
            ("Spa Package", 60, 70, True),
        ],
    },
    {
        "name": f"{SYNTHETIC_TENANT_PREFIX} Salon 2 {SYNTHETIC_TENANT_SUFFIX}",
        "business_type": Tenant.BusinessType.SALON,
        "staff": ["Staff C", "Staff D", "Staff E"],
        "services": [
            ("Basic Trim", 20, 15, False),
            ("Bridal Styling", 120, 200, True),
        ],
    },
    {
        "name": f"{SYNTHETIC_TENANT_PREFIX} Clinic {SYNTHETIC_TENANT_SUFFIX}",
        "business_type": Tenant.BusinessType.CLINIC,
        "staff": ["Dr. A", "Dr. B"],
        "services": [
            ("General Checkup", 20, 40, False),
            ("Dental Cleaning", 45, 60, True),
            ("Specialist Consultation", 30, 80, True),
        ],
    },
    {
        "name": f"{SYNTHETIC_TENANT_PREFIX} Consultancy {SYNTHETIC_TENANT_SUFFIX}",
        "business_type": Tenant.BusinessType.CONSULTANT,
        "staff": ["Consultant A", "Consultant B"],
        "services": [
            ("Strategy Session", 60, 100, True),
            ("Quick Advisory Call", 30, 40, False),
        ],
    },
]

TOTAL_BOOKINGS_TARGET = 800
HISTORY_DAYS = 90  # ~3 months
CUSTOMERS_PER_TENANT = 45  # ~180 total across 4 tenants

PERSONA_WEIGHTS = {
    "reliable": 0.60,
    "casual": 0.25,
    "chronic_no_show": 0.15,
}

BUSINESS_OPEN_HOUR = 9
BUSINESS_CLOSE_HOUR = 18

CHANNEL_CHOICES_BY_PERSONA = {
    # Reliable customers lean online/app; chronic no-shows lean walk-in/phone —
    # this mirrors the commitment-friction reasoning used in risk.py's stub,
    # so the synthetic data and the rule-based stub are telling a consistent
    # real-world story, even though the model will be trained independently.
    "reliable": [("online", 0.55), ("app", 0.25), ("phone", 0.15), ("walk_in", 0.05)],
    "casual": [("online", 0.35), ("app", 0.20), ("phone", 0.25), ("walk_in", 0.20)],
    "chronic_no_show": [("online", 0.15), ("app", 0.10), ("phone", 0.30), ("walk_in", 0.45)],
}


def weighted_choice(choices):
    """choices: list of (value, weight) tuples."""
    values, weights = zip(*choices)
    return random.choices(values, weights=weights, k=1)[0]


def assign_persona():
    return weighted_choice(list(PERSONA_WEIGHTS.items()))


def sample_lead_time_hours(persona):
    """Reliable customers tend to book further ahead; chronic no-shows
    tend to book last-minute — with overlapping ranges, not hard cutoffs,
    so the signal is realistic rather than a clean rule."""
    if persona == "reliable":
        return max(1, random.gauss(96, 60))   # centered ~4 days out
    if persona == "casual":
        return max(1, random.gauss(48, 40))   # centered ~2 days out
    return max(0.5, random.gauss(10, 12))     # chronic_no_show: centered ~10h out


def decide_deposit_paid(persona, deposit_required):
    if not deposit_required:
        return False
    if persona == "reliable":
        return random.random() < 0.92
    if persona == "casual":
        return random.random() < 0.55
    return random.random() < 0.20  # chronic_no_show


def decide_no_show(persona, lead_time_hours, deposit_required, deposit_paid, channel):
    """The actual outcome probability — combines persona with the same
    real-world factors the rule-based stub uses, plus noise. This is the
    ground truth the model will be trained to recover; it is intentionally
    NOT a clean function of any single field.
    """
    base = {"reliable": 0.04, "casual": 0.18, "chronic_no_show": 0.55}[persona]

    if lead_time_hours < 6:
        base += 0.18
    elif lead_time_hours < 24:
        base += 0.08

    if deposit_required and not deposit_paid:
        base += 0.20
    elif deposit_required and deposit_paid:
        base -= 0.05

    if channel in ("walk_in", "phone"):
        base += 0.05

    base += random.gauss(0, 0.07)  # noise — keeps this from being a clean rule
    return min(max(base, 0.01), 0.97)


class Command(BaseCommand):
    help = "Generate an isolated synthetic training dataset for the no-show classifier (Step 5)."

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete all existing synthetic tenants (and their data) before regenerating.',
        )

    def handle(self, *args, **options):
        if options['reset']:
            self._reset_synthetic_data()

        random.seed(42)  # reproducible runs — same dataset every time unless --reset is re-run with different code

        tenants = self._create_tenants_staff_services()
        customers_by_tenant = self._create_customers(tenants)

        total_created = self._create_bookings(tenants, customers_by_tenant)

        self.stdout.write(self.style.SUCCESS(
            f"\nDone. Created {total_created} synthetic bookings across {len(tenants)} tenants."
        ))
        self.stdout.write(
            "These tenants are clearly labeled 'Synthetic ... — Training Data' "
            "and are isolated from your real tenant data."
        )

    def _reset_synthetic_data(self):
        existing = Tenant.objects.filter(name__startswith=SYNTHETIC_TENANT_PREFIX)
        count = existing.count()
        existing.delete()  # cascades to staff, services, customers, bookings
        self.stdout.write(f"Reset: deleted {count} existing synthetic tenant(s) and their data.")

    @transaction.atomic
    def _create_tenants_staff_services(self):
        tenants = []
        for blueprint in TENANT_BLUEPRINTS:
            tenant, _ = Tenant.objects.get_or_create(
                name=blueprint["name"],
                defaults={"business_type": blueprint["business_type"], "timezone": "UTC"},
            )
            staff_objs = [
                StaffMember.objects.get_or_create(tenant=tenant, name=name, defaults={"role": "Staff"})[0]
                for name in blueprint["staff"]
            ]
            service_objs = [
                Service.objects.get_or_create(
                    tenant=tenant, name=name,
                    defaults={"duration_minutes": dur, "price": price, "requires_deposit": dep},
                )[0]
                for name, dur, price, dep in blueprint["services"]
            ]
            tenants.append({"tenant": tenant, "staff": staff_objs, "services": service_objs})
            self.stdout.write(f"  Tenant ready: {tenant.name} ({len(staff_objs)} staff, {len(service_objs)} services)")
        return tenants

    @transaction.atomic
    def _create_customers(self, tenants):
        customers_by_tenant = {}
        for t in tenants:
            tenant = t["tenant"]
            customers = []
            for i in range(CUSTOMERS_PER_TENANT):
                persona = assign_persona()
                customer = Customer.objects.create(
                    tenant=tenant,
                    name=f"Synthetic Customer {tenant.id}-{i}",
                    phone=f"9{tenant.id:02d}{i:05d}0000"[:10],
                    email="",
                )
                # Persona is tracked only in-memory for THIS generation run —
                # never written to the Customer model. The model must learn
                # from booking behavior, not from a hidden label.
                customers.append({"customer": customer, "persona": persona})
            customers_by_tenant[tenant.id] = customers
            self.stdout.write(f"  {len(customers)} synthetic customers created for {tenant.name}")
        return customers_by_tenant

    @transaction.atomic
    def _create_bookings(self, tenants, customers_by_tenant):
        now = timezone.now()
        bookings_per_tenant = TOTAL_BOOKINGS_TARGET // len(tenants)
        total_created = 0

        for t in tenants:
            tenant = t["tenant"]
            staff_list = t["staff"]
            service_list = t["services"]
            customers = customers_by_tenant[tenant.id]

            created_for_tenant = 0
            attempts = 0
            # Loop with an attempts cap rather than a fixed range, since a
            # small fraction of attempts are skipped (see appointment_dt
            # bounds check below) and we want a true count, not an estimate.
            while created_for_tenant < bookings_per_tenant and attempts < bookings_per_tenant * 3:
                attempts += 1
                entry = random.choice(customers)
                customer, persona = entry["customer"], entry["persona"]
                staff = random.choice(staff_list)
                service = random.choice(service_list)

                lead_time_hours = sample_lead_time_hours(persona)

                # Spread appointment_datetime across the past HISTORY_DAYS,
                # weighted toward weekday afternoons for realism.
                days_ago = random.uniform(0, HISTORY_DAYS)
                appointment_dt = now - timedelta(days=days_ago)
                hour = int(random.triangular(BUSINESS_OPEN_HOUR, BUSINESS_CLOSE_HOUR, 14))
                appointment_dt = appointment_dt.replace(hour=hour, minute=random.choice([0, 30]), second=0, microsecond=0)

                created_at = appointment_dt - timedelta(hours=lead_time_hours)
                if created_at >= now:
                    continue  # would imply the booking was made in the future — skip, resample

                channel = weighted_choice(CHANNEL_CHOICES_BY_PERSONA[persona])
                deposit_paid = decide_deposit_paid(persona, service.requires_deposit)

                no_show_probability = decide_no_show(
                    persona, lead_time_hours, service.requires_deposit, deposit_paid, channel
                )
                is_no_show = random.random() < no_show_probability
                status = Booking.Status.NO_SHOW if is_no_show else Booking.Status.COMPLETED

                booking = Booking(
                    tenant=tenant,
                    customer=customer,
                    staff=staff,
                    service=service,
                    appointment_datetime=appointment_dt,
                    booking_channel=channel,
                    deposit_required=service.requires_deposit,
                    deposit_paid=deposit_paid,
                    status=status,
                )
                # created_at is auto_now_add — Django sets it to the current
                # moment at INSERT time regardless of what we assign here,
                # and Booking.save() computes lead_time_hours from THAT
                # (wrong, "now"-based) created_at in the same save(). So we
                # must backdate created_at AND recompute lead_time_hours
                # together afterward — updating created_at alone would
                # leave a stale, nonsensical lead_time_hours behind exactly
                # as it did on the first run of this generator.
                booking.save()
                correct_lead_time_hours = round((appointment_dt - created_at).total_seconds() / 3600, 2)
                Booking.objects.filter(pk=booking.pk).update(
                    created_at=created_at,
                    lead_time_hours=correct_lead_time_hours,
                )

                created_for_tenant += 1
                total_created += 1

            self.stdout.write(f"  {created_for_tenant} bookings created for {tenant.name}")

        return total_created