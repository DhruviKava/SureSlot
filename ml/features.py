"""
Feature extraction for the no-show classifier.

This module is intentionally shared between training (ml/train_model.py)
and prediction (bookings/risk.py, once Step 5 wires the real model in).
Using the exact same function in both places matters: if training built
features one way and prediction built them slightly differently, the
model would silently perform worse (or wrongly) in production than its
evaluation metrics suggested — a classic, easy-to-miss ML bug.

Feature set (deliberately the same fields the rule-based stub used, so
the two approaches are directly comparable):
    - lead_time_hours          (numeric)
    - deposit_required         (0/1)
    - deposit_paid             (0/1)
    - booking_channel          (one-hot encoded: online/phone/walk_in/app)
    - customer_no_show_rate    (numeric, computed from PRIOR resolved
                                 bookings only — see note below)

IMPORTANT — temporal leakage note:
customer_no_show_rate must only be computed from bookings that happened
BEFORE the booking being featurized, not from the customer's entire
history including future bookings. Using future information to predict
the past is a textbook data-leakage bug that would make evaluation
metrics look better than the model could ever perform in real use, where
the future genuinely is not yet known. This module computes history
strictly as "prior bookings at the time this one was made."
"""

import pandas as pd

CHANNEL_CATEGORIES = ["online", "phone", "walk_in", "app"]


def build_feature_rows(bookings_queryset):
    """Given a queryset of resolved Booking objects (completed/no_show),
    return a pandas DataFrame of features plus the label column 'no_show'.

    Bookings are processed in chronological order (by created_at) so that
    each booking's customer_no_show_rate can be computed strictly from
    that customer's PRIOR bookings only, avoiding temporal leakage.
    """
    bookings = list(bookings_queryset.select_related('customer', 'tenant').order_by('created_at'))

    # Track each customer's running history as we walk through time —
    # this is what guarantees no booking ever "sees" a later booking's
    # outcome when computing its own history feature.
    customer_running_totals = {}  # customer_id -> [total, no_shows]

    rows = []
    for booking in bookings:
        cust_id = booking.customer_id
        total, no_shows = customer_running_totals.get(cust_id, (0, 0))
        customer_no_show_rate = (no_shows / total) if total > 0 else 0.4  # same no-history default as risk.py's stub

        row = {
            "booking_id": booking.id,
            "lead_time_hours": booking.lead_time_hours if booking.lead_time_hours is not None else 0.0,
            "deposit_required": int(booking.deposit_required),
            "deposit_paid": int(booking.deposit_paid),
            "booking_channel": booking.booking_channel,
            "customer_no_show_rate": customer_no_show_rate,
            "no_show": 1 if booking.status == "no_show" else 0,
            "is_synthetic": 1 if booking.tenant.name.startswith("Synthetic") else 0,
        }
        rows.append(row)

        # NOW update this customer's running totals, AFTER using the
        # pre-update value above — so this booking's own outcome only
        # affects FUTURE bookings' history feature, never its own.
        total += 1
        no_shows += 1 if booking.status == "no_show" else 0
        customer_running_totals[cust_id] = (total, no_shows)

    df = pd.DataFrame(rows)
    return one_hot_encode_channel(df)


def one_hot_encode_channel(df):
    """One-hot encode booking_channel into fixed columns matching
    CHANNEL_CATEGORIES, so the column set is identical between training
    and any single-row prediction later — pandas' get_dummies() alone
    would produce different columns depending on which channels happen
    to appear in a given batch, which breaks at prediction time when a
    single booking only has ONE channel value.
    """
    for category in CHANNEL_CATEGORIES:
        df[f"channel_{category}"] = (df["booking_channel"] == category).astype(int)
    return df


def feature_columns():
    """The exact, ordered list of columns the model is trained on —
    used both by training and by risk.py at prediction time, so a
    single row can be built with the same column order every time.
    """
    return [
        "lead_time_hours",
        "deposit_required",
        "deposit_paid",
        "customer_no_show_rate",
    ] + [f"channel_{c}" for c in CHANNEL_CATEGORIES]