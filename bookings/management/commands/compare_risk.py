"""
Management command: compare the rule-based stub against the trained
model, side by side, for one or more bookings — and optionally run a
bulk accuracy comparison across many resolved bookings at once.

This is the evidence-gathering tool for the README / portfolio: it
answers "did training an actual model improve on the hand-written rule?"
with real numbers instead of a claim.

Usage:
    python manage.py compare_risk 22 23              # specific bookings
    python manage.py compare_risk --bulk             # summary across all
                                                        resolved synthetic
                                                        bookings
    python manage.py compare_risk --bulk --tenant 1  # restrict to one tenant
"""

from django.core.management.base import BaseCommand

from bookings.models import Booking
from bookings.risk import score_booking, score_booking_stub, risk_bucket, model_is_available


class Command(BaseCommand):
    help = "Compare the rule-based risk stub against the trained model, side by side."

    def add_arguments(self, parser):
        parser.add_argument(
            'booking_ids',
            nargs='*',
            type=int,
            help='Specific Booking primary keys to compare.',
        )
        parser.add_argument(
            '--bulk',
            action='store_true',
            help='Run an accuracy comparison across all resolved bookings instead of listing individual ones.',
        )
        parser.add_argument(
            '--tenant',
            type=int,
            default=None,
            help='Restrict --bulk comparison to one tenant ID.',
        )

    def handle(self, *args, **options):
        if not model_is_available():
            self.stdout.write(self.style.WARNING(
                "No trained model is currently loaded — both columns below will show "
                "the SAME stub score. Run 'python ml/train_model.py' first."
            ))

        if options['bulk']:
            self._run_bulk_comparison(options['tenant'])
        elif options['booking_ids']:
            self._run_individual_comparison(options['booking_ids'])
        else:
            self.stderr.write(self.style.ERROR(
                "Provide booking IDs to compare, or pass --bulk for an aggregate comparison."
            ))

    def _run_individual_comparison(self, booking_ids):
        header = f"{'ID':>5} | {'Customer / Service':<35} | {'Actual':<10} | {'Stub':>7} | {'Model':>7} | Agreement"
        self.stdout.write(header)
        self.stdout.write("-" * len(header))

        for booking_id in booking_ids:
            try:
                booking = Booking.objects.select_related('customer', 'service').get(pk=booking_id)
            except Booking.DoesNotExist:
                self.stderr.write(self.style.ERROR(f"No booking with id={booking_id} found."))
                continue

            stub_score = score_booking_stub(booking)
            model_score = score_booking(booking)
            stub_bucket = risk_bucket(stub_score)
            model_bucket = risk_bucket(model_score)
            agree = "same bucket" if stub_bucket == model_bucket else f"{stub_bucket}->{model_bucket}"

            label = f"{booking.customer.name} / {booking.service.name}"[:35]
            actual = booking.status if booking.status in (Booking.Status.COMPLETED, Booking.Status.NO_SHOW) else f"({booking.status})"

            self.stdout.write(
                f"{booking.id:>5} | {label:<35} | {actual:<10} | "
                f"{stub_score:>7.3f} | {model_score:>7.3f} | {agree}"
            )

    def _run_bulk_comparison(self, tenant_id):
        qs = Booking.objects.filter(status__in=[Booking.Status.COMPLETED, Booking.Status.NO_SHOW])
        if tenant_id:
            qs = qs.filter(tenant_id=tenant_id)

        bookings = list(qs.select_related('customer'))
        total = len(bookings)
        if total == 0:
            self.stderr.write(self.style.ERROR("No resolved bookings found to compare."))
            return

        self.stdout.write(f"Comparing stub vs model across {total} resolved bookings...\n")

        # "Correct" here means: the score was on the right side of 0.5
        # relative to what actually happened. This is a simple way to
        # turn a probability into a yes/no judgment for a quick aggregate
        # comparison — the full metrics in ml/training_report.md (AUC,
        # calibration, etc.) are the more rigorous version of this same
        # question, computed properly on a held-out test set.
        stub_correct = 0
        model_correct = 0
        for booking in bookings:
            actual_no_show = booking.status == Booking.Status.NO_SHOW
            stub_predicts_no_show = score_booking_stub(booking) >= 0.5
            model_predicts_no_show = score_booking(booking) >= 0.5

            if stub_predicts_no_show == actual_no_show:
                stub_correct += 1
            if model_predicts_no_show == actual_no_show:
                model_correct += 1

        self.stdout.write(f"  Stub accuracy:  {stub_correct}/{total}  ({100*stub_correct/total:.1f}%)")
        self.stdout.write(f"  Model accuracy: {model_correct}/{total}  ({100*model_correct/total:.1f}%)")
        diff = model_correct - stub_correct
        if diff > 0:
            self.stdout.write(self.style.SUCCESS(
                f"\n  The trained model correctly classified {diff} more booking(s) than the stub."
            ))
        elif diff < 0:
            self.stdout.write(self.style.WARNING(
                f"\n  The stub correctly classified {-diff} more booking(s) than the model on this set."
            ))
        else:
            self.stdout.write("\n  Both approaches matched on accuracy for this set.")
        self.stdout.write(
            "\n  Note: this is a quick same-data sanity check, not a substitute for "
            "the held-out test-set evaluation in ml/training_report.md — a model can "
            "look good here simply by having seen this exact data during training."
        )