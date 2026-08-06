# SureSlot — Multi-Tenant Appointment System with No-Show Prediction

SureSlot is a production-ready, multi-tenant appointment scheduling platform that goes beyond standard scheduling. Instead of just sending automated email reminders, SureSlot uses a **Machine Learning (ML) classifier** to predict the likelihood of an appointment ending in a "no-show" (missed appointment) before it even happens, allowing business owners to proactively protect their revenue.

---

## 💡 The Problem & The Solution

### The Problem

For service-oriented businesses (such as hair salons, dental clinics, and consultancies), missed appointments represent **pure lost revenue**. Unlike physical products, a vacant time slot cannot be "restocked" or sold later.

Existing scheduling solutions (like Calendly, Fresha, or Square Appointments) only focus on booking creation and static reminders. They do not analyze historical patterns to predict who is likely to miss their appointment.

### The Solution

SureSlot provides:

1. **Risk-Colored Dashboard**: A month-view calendar dashboard that visually flags scheduled bookings with a risk level: **Low** (green), **Medium** (yellow), or **High** (red).
2. **Actionable Insights**: Helps business owners make decisions such as calling high-risk customers, requiring deposits, or double-booking risky slots.
3. **Calibrated Predictions**: An integrated ML model that predicts no-show probability based on multiple commitment and history features.

---

## 📂 Folder Structure & Key Files

The project is structured as a standard Django project split into clean, modular applications:

```text
SureSlot/
├── accounts/                  # Auth app: Custom email-based user tied to a Tenant
│   ├── models.py              # Custom User model
│   ├── views.py               # Custom login / logout view logic
│   └── urls.py                # Account routing
├── tenants/                   # Business administration app
│   ├── models.py              # Tenant, StaffMember, Service, and Customer models
│   ├── views.py               # Management views for services & staff
│   └── urls.py                # Tenant management routing
├── bookings/                  # Core booking management
│   ├── models.py              # Booking model (acts as DB record & ML feature store)
│   ├── views.py               # Tenant calendar dashboard & AJAX time-slot APIs
│   ├── risk.py                # Inference hub: scores bookings using ML model/stub fallback
│   ├── urls.py                # Booking and calendar routing
│   └── management/commands/   # Custom management commands
│       ├── generate_training_data.py  # Generates realistic synthetic customer/booking data
│       ├── compare_risk.py            # Compares model accuracy vs. heuristic stub
│       └── explain_risk.py            # Explains feature contributions to risk scores
├── core/                      # Marketing landing page
│   ├── views.py               # Public home view (auto-redirects logged-in users)
│   └── urls.py                # Landing page routing
├── ml/                        # Machine Learning pipeline
│   ├── features.py            # Feature engineering (chronological to avoid data leakage)
│   ├── train_model.py         # Model training script
│   ├── training_report.md     # Auto-generated report containing model metrics
│   └── trained_models/        # Saved joblib classifiers and metadata
├── static/                    # Global stylesheet assets
│   ├── css/
│   │   ├── base.css           # Global design tokens & variables
│   │   ├── dashboard.css      # Sidebar & calendar layout styles
│   │   └── booking_form.css   # Interactive client booking layout
│   └── js/
│       └── booking_form.js    # Client-side AJAX slot fetching & selection
├── templates/                 # Global HTML templates
│   ├── base.html              # Layout parent template loading base styles & JQuery
│   ├── accounts/              # Sign-in templates
│   ├── bookings/              # Calendar dashboard, booking form, confirmation pages
│   ├── core/                  # Landing page
│   └── tenants/               # Staff & service management grids
├── db_booking                 # SQLite development database
├── manage.py                  # Django administrative script
└── requirements.txt           # Python dependencies
```

---

## 🤖 Why Machine Learning (ML)?

### Heuristic Rule-Based vs. Machine Learning

A rule-based heuristic (like the one implemented in `bookings/risk.py` as `score_booking_stub`) uses hard-coded weights to evaluate a booking (e.g., adding `0.3` to the risk score if no deposit is paid).

While useful as a baseline, rule-based systems:

- Do not adapt to real data patterns.
- Scale poorly when adding features.
- Fail to capture non-linear relationships.

A **Machine Learning model** (Logistic Regression in this project) learns feature weights mathematically from historical data. It analyzes how different features interact (e.g. how a walk-in booking channel correlates with no-shows differently depending on the customer's prior completion rate).

### The Feature Set

The model is trained on the following features extracted by `ml/features.py`:

1. **`lead_time_hours`**: The hours elapsed between creating the booking and the appointment start (shorter lead times often correlate with higher commitment, whereas long lead times have more decay).
2. **`deposit_required`** & **`deposit_paid`**: Financial commitment signals. Requiring a deposit but leaving it unpaid is the strongest signal of high no-show risk.
3. **`booking_channel`**: One-hot encoded channels (`online`, `phone`, `walk_in`, `app`).
4. **`customer_no_show_rate`**: The historical no-show rate of the customer, computed **chronologically** up to the time the booking was created to avoid temporal data leakage.

### Preventing Data Leakage

When training an ML model on time-series/historical records, it is critical to ensure that a customer's history at booking time is calculated using *only* resolved bookings that occurred *prior* to that booking. Calculating no-show rates using the customer's entire history (including bookings in the future) introduces **temporal leakage**, making training metrics look artificially high while failing in production. `ml/features.py` prevents this by walking the dataset chronologically and maintaining a running total.

---

## ⚙️ Setup & Execution

### 1. Prerequisites

Ensure you have Python 3.10+ installed.

### 2. Installation

```bash
# Clone the repository and navigate to root
cd booking-core

# Set up virtual environment
python -m venv bookingenv
source bookingenv/bin/activate          # Windows: bookingenv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Database migrations

```bash
python manage.py migrate
python manage.py createsuperuser
```

### 4. Populate Demo & Synthetic Data

Generate a synthetic dataset of ~800 historical bookings spread across the past 3 months to simulate real-world operations:

```bash
python manage.py generate_training_data --reset
```

### 5. Train the ML Model

Train the Logistic Regression classifier on the synthetic training dataset:

```bash
python ml/train_model.py
```

This script evaluates the model, saves it to `ml/trained_models/no_show_model.joblib`, and outputs a detailed performance analysis to `ml/training_report.md`.

### 6. Run the Server

```bash
python manage.py runserver
```

Visit `/dashboard/` to view the calendar with risk-colored appointment cards.

---

## 📊 Management Commands

- **`generate_training_data`**: Wipes and generates isolated synthetic tenants, customers, and bookings driven by realistic customer personas (reliable vs casual vs chronic no-shows).
- **`compare_risk`**: Compares the rule-based stub and the trained ML model side-by-side or globally across all resolved bookings:
  ```bash
  python manage.py compare_risk --bulk
  ```
- **`explain_risk`**: Inspects a specific booking ID and prints a feature-by-feature explanation of how the model arrived at its predicted probability.
