from typing import TypedDict

from celery import Celery

from ingestion.config import get_settings


class SmokeResult(TypedDict):
    status: str
    worker: str


settings = get_settings()
celery_app = Celery(
    "storecipe-ingestion",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 900},
    result_expires=300,
    task_track_started=True,
)


@celery_app.task(name="ingestion.smoke")  # type: ignore[untyped-decorator]
def smoke() -> SmokeResult:
    """Verify that the API, Redis, and worker are wired together."""

    return {"status": "ok", "worker": "ingestion"}
