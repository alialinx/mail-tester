import os
from celery import Celery

broker = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
backend = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

celery_app = Celery(
    "mailtester",
    broker=broker,
    backend=backend,
)

# 🔴 BU SATIR ŞART
celery_app.autodiscover_tasks(["src.worker"])

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
