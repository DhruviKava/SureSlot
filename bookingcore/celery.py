"""
Celery application setup for Booking Core.

This file is the entry point Celery uses. It must live here (in the
project package, alongside settings.py) so Celery can find Django's
settings before any tasks are imported.

To run the worker locally:
    celery -A bookingcore worker -l info

To run the scheduler (beats — fires periodic tasks on a schedule):
    celery -A bookingcore beat -l info

Both must be running at the same time in separate terminals during
development. In production (Render/Railway) each runs as a separate
process/service pointing at the same Redis instance.
"""
import os

from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'bookingcore.settings')

app = Celery('bookingcore')

# Read Celery config from Django settings — any key prefixed with
# CELERY_ in settings.py is picked up automatically.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks.py in every INSTALLED_APP — you never need to
# manually register tasks, just create tasks.py in an app and Celery
# finds it.
app.autodiscover_tasks()