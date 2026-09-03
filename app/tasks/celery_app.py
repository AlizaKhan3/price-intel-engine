from __future__ import annotations

"""
Celery app. This is what turns "sync catalog", "scrape competitors",
"run matching", and "recompute comparisons" into scheduled background
jobs instead of things you'd have to trigger by hand.

Run a worker with:   celery -A app.tasks.celery_app worker --loglevel=info
Run the scheduler:    celery -A app.tasks.celery_app beat --loglevel=info

For the free-tier MVP, Redis can be a free Upstash instance. Both
commands above can run as free-tier background workers on Render or
Railway.
"""
from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery("price_intel", broker=settings.REDIS_URL, backend=settings.REDIS_URL)

celery_app.conf.beat_schedule = {
    "sync-catalog-every-6-hours": {
        "task": "app.tasks.scheduled_jobs.sync_catalog_task",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "scrape-competitors-every-6-hours": {
        "task": "app.tasks.scheduled_jobs.scrape_competitors_task",
        "schedule": crontab(minute=30, hour="*/6"),
    },
    "run-matching-daily": {
        "task": "app.tasks.scheduled_jobs.run_matching_task",
        "schedule": crontab(minute=0, hour=3),
    },
    "recompute-comparisons-every-6-hours": {
        "task": "app.tasks.scheduled_jobs.recompute_comparisons_task",
        "schedule": crontab(minute=45, hour="*/6"),
    },
}
celery_app.conf.timezone = "Asia/Karachi"

celery_app.autodiscover_tasks(["app.tasks"])
