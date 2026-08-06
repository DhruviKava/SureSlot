"""
No-show risk scoring.

Two scoring approaches live here side by side:

1. `score_booking_stub()` — the original Phase 4 rule-based heuristic.
   Kept (not deleted) so it can be directly compared against the real
   model — see ml/training_report.md and the `compare_risk` management
   command for that comparison.

2. `score_booking()` — the REAL entry point every caller uses. As of
   Step 5, this loads the trained logistic regression model (see
   ml/train_model.py) and calls predict_proba() on it. If no trained
   model file is found (e.g. a fresh clone of this repo before anyone
   has run `python ml/train_model.py`), it falls back to the rule-based
   stub automatically, so the app never hard-crashes for missing a
   model file — it just quietly runs in stub mode and logs a warning.

The model and the stub deliberately use the SAME underlying booking
fields, which is why the feature-extraction logic in ml/features.py
mirrors the factor logic below as closely as possible.
"""

import logging
import os

import joblib
import pandas as pd

from bookings.models import Booking

logger = logging.getLogger(__name__)

# Tunable weights for the rule-based stub — deliberately simple and named
# so the rule is explainable in one sentence per factor. Not fitted to any
# data; chosen to produce plausible-looking spread for demo purposes.
LEAD_TIME_WEIGHT = 0.35
DEPOSIT_WEIGHT = 0.30
CHANNEL_WEIGHT = 0.15
HISTORY_WEIGHT = 0.20

RISK_BUCKETS = (
    (0.66, "high"),
    (0.33, "medium"),
    (0.0, "low"),
)

# --- Trained model loading ---
_ML_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml", "trained_models")
_MODEL_PATH = os.path.join(_ML_DIR, "no_show_model.joblib")
_METADATA_PATH = os.path.join(_ML_DIR, "model_metadata.joblib")

_model = None
_model_metadata = None
_model_load_attempted = False


def _load_model():
    """Lazily load the trained model once per process, not once per
    request — joblib.load() reads a file from disk, which is needlessly
    slow to repeat on every single dashboard render.
    """
    global _model, _model_metadata, _model_load_attempted
    if _model_load_attempted:
        return _model
    _model_load_attempted = True
    try:
        _model = joblib.load(_MODEL_PATH)
        _model_metadata = joblib.load(_METADATA_PATH)
        logger.info("Loaded trained no-show model (trained_at=%s)", _model_metadata.get("trained_at"))
    except FileNotFoundError:
        logger.warning(
            "No trained model found at %s — falling back to the rule-based "
            "risk stub. Run 'python ml/train_model.py' to train one.",
            _MODEL_PATH,
        )
        _model = None
    return _model


def model_is_available():
    """Whether a trained model is actually loaded and in use right now —
    exposed so views/templates can optionally indicate which scoring mode
    is active, rather than silently pretending it's always the real model.
    """
    return _load_model() is not None


def _lead_time_factor(lead_time_hours):
    """Short notice -> higher risk. Returns 0.0-1.0."""
    if lead_time_hours is None:
        return 0.5  # unknown lead time -> treat as medium, not an edge case
    if lead_time_hours < 6:
        return 1.0
    if lead_time_hours < 24:
        return 0.6
    if lead_time_hours < 72:
        return 0.25
    return 0.05


def _deposit_factor(deposit_required, deposit_paid):
    """Paid deposit = strong commitment signal -> low risk.
    Required-but-unpaid is a real warning sign -> high risk.
    No deposit involved at all is neutral -> medium.
    """
    if deposit_required and deposit_paid:
        return 0.05
    if deposit_required and not deposit_paid:
        return 0.9
    return 0.5


def _channel_factor(channel):
    """Walk-in/phone bookings carry slightly less commitment friction
    than online/app bookings, which require more deliberate action."""
    return {
        Booking.BookingChannel.ONLINE: 0.3,
        Booking.BookingChannel.APP: 0.3,
        Booking.BookingChannel.PHONE: 0.55,
        Booking.BookingChannel.WALK_IN: 0.6,
    }.get(channel, 0.5)


def _history_factor(customer, tenant_id, exclude_booking_id=None):
    """A customer's own past no-show rate, as of right now. Recalculated
    live (not stored) since this is a live forecast about the future, not
    a snapshot of a past fact.

    Note this is intentionally different from ml/features.py's chronological
    walk during TRAINING: training had to avoid letting a booking "see" its
    own future, since it was scoring many historical bookings against a
    moving point in time. Here, at live prediction time, there genuinely is
    no future yet relative to right now — every resolved booking for this
    customer really did happen before this moment, so using all of them is
    correct, not a leak.
    """
    past = Booking.objects.filter(
        tenant_id=tenant_id,
        customer_id=customer.id,
        status__in=[Booking.Status.COMPLETED, Booking.Status.NO_SHOW],
    )
    if exclude_booking_id:
        past = past.exclude(pk=exclude_booking_id)

    total = past.count()
    if total == 0:
        return 0.4  # no history yet -> slightly cautious default, not zero

    no_shows = past.filter(status=Booking.Status.NO_SHOW).count()
    return min(no_shows / total, 1.0)


def score_booking_stub(booking):
    """The original Phase 4 rule-based heuristic. Kept for direct
    comparison against the real model — see the `compare_risk` command.
    """
    lead = _lead_time_factor(booking.lead_time_hours)
    deposit = _deposit_factor(booking.deposit_required, booking.deposit_paid)
    channel = _channel_factor(booking.booking_channel)
    history = _history_factor(booking.customer, booking.tenant_id, exclude_booking_id=booking.id)

    score = (
        lead * LEAD_TIME_WEIGHT
        + deposit * DEPOSIT_WEIGHT
        + channel * CHANNEL_WEIGHT
        + history * HISTORY_WEIGHT
    )
    return round(min(max(score, 0.0), 1.0), 3)


def _build_model_input_row(booking):
    """Build a single-row feature DataFrame for one booking, in the exact
    column order the model was trained on. Mirrors ml/features.py's
    per-row logic, but for ONE live booking rather than a historical batch.
    """
    history_rate = _history_factor(booking.customer, booking.tenant_id, exclude_booking_id=booking.id)

    row = {
        "lead_time_hours": booking.lead_time_hours if booking.lead_time_hours is not None else 0.0,
        "deposit_required": int(booking.deposit_required),
        "deposit_paid": int(booking.deposit_paid),
        "customer_no_show_rate": history_rate,
        "channel_online": int(booking.booking_channel == Booking.BookingChannel.ONLINE),
        "channel_phone": int(booking.booking_channel == Booking.BookingChannel.PHONE),
        "channel_walk_in": int(booking.booking_channel == Booking.BookingChannel.WALK_IN),
        "channel_app": int(booking.booking_channel == Booking.BookingChannel.APP),
    }
    feature_cols = _model_metadata["feature_columns"]
    return pd.DataFrame([row])[feature_cols]


def score_booking(booking):
    """Return a no-show risk score in [0.0, 1.0] for one Booking.

    This is the single entry point every caller (views, templates, the
    explain_risk command) uses. Uses the trained model if one is loaded;
    automatically falls back to the rule-based stub otherwise, so a fresh
    clone of this repo without a trained model file still works.
    """
    model = _load_model()
    if model is None:
        return score_booking_stub(booking)

    X = _build_model_input_row(booking)
    probability = model.predict_proba(X)[0][1]  # probability of class "1" = no_show
    return round(float(probability), 3)


def risk_bucket(score):
    """Map a 0-1 score to a 'low' / 'medium' / 'high' label for display.
    A raw probability isn't actionable on its own — the bucket is what the
    dashboard actually renders as a badge.
    """
    for threshold, label in RISK_BUCKETS:
        if score >= threshold:
            return label
    return "low"