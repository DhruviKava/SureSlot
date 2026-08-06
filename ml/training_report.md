# No-Show Model — Training Report

Trained: 2026-07-06 09:51

Training data: 808 resolved bookings (800 synthetic, 8 real)

## Segmented Evaluation Metrics

| Metric | Overall Test Set | Synthetic Segment | Real Customer Segment |
|---|---|---|---|
| accuracy | 0.889 | 0.887 | 1.000 |
| precision | 0.795 | 0.784 | 1.000 |
| recall | 0.756 | 0.744 | 1.000 |
| f1 | 0.775 | 0.763 | 1.000 |
| roc_auc | 0.908 | 0.905 | nan |

## Feature importance

| Feature | Coefficient | Effect |
|---|---|---|
| customer_no_show_rate | +1.694 | raises risk |
| deposit_paid | -1.461 | lowers risk |
| deposit_required | +1.099 | raises risk |
| channel_app | -0.802 | lowers risk |
| channel_walk_in | +0.377 | raises risk |
| channel_phone | +0.343 | raises risk |
| channel_online | +0.085 | raises risk |
| lead_time_hours | -0.010 | lowers risk |

## Calibration (predicted risk bucket vs actual outcome rate)

| bin             |   avg_predicted |   actual_rate |   count |
|:----------------|----------------:|--------------:|--------:|
| (0.0206, 0.199] |        0.106447 |     0.0410959 |      73 |
| (0.199, 0.375]  |        0.280216 |     0.0769231 |      39 |
| (0.375, 0.552]  |        0.465865 |     0.533333  |      15 |
| (0.552, 0.728]  |        0.645116 |     0.666667  |      18 |
| (0.728, 0.904]  |        0.830466 |     0.882353  |      17 |

*Generated automatically by ml/train_model.py — do not edit by hand.*
