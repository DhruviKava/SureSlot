try:
	from .celery import app as celery_app
except ImportError:
	# Allow Django management commands to run when Celery isn't installed
	celery_app = None

__all__ = ('celery_app',)
