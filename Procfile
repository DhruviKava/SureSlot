web: gunicorn bookingcore.wsgi --workers 2 --timeout 120
worker: celery -A bookingcore worker --loglevel=info --concurrency=2
release: python manage.py migrate --noinput && python manage.py collectstatic --noinput