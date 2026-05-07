import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'coding_platform.settings')

app = Celery('coding_platform')

app.config_from_object('django.conf:settings', namespace='CELERY')

# 🔥 ADD THIS SAFETY FIX
app.conf.broker_connection_retry_on_startup = True
app.conf.broker_connection_retry = True
app.conf.broker_connection_max_retries = None

app.autodiscover_tasks()
