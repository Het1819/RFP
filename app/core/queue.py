import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.job import ProcessingJob

logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[Any]] = set()


def create_processing_job(
    db: Session,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
    job_type: str,
    user_id: uuid.UUID | None,
) -> ProcessingJob:
    from app.core.observability import request_id_var

    job = ProcessingJob(
        org_id=org_id,
        project_id=project_id,
        document_id=document_id,
        job_type=job_type,
        status="QUEUED",
        created_by_user_id=user_id,
        attempts=0,
        max_attempts=settings.JOB_MAX_RETRIES,
        request_id=request_id_var.get(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


async def enqueue_to_redis(job_id: uuid.UUID) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await redis.enqueue_job("process_document_task", str(job_id))
    await redis.close()


def enqueue_job(
    db: Session,
    org_id: uuid.UUID,
    project_id: uuid.UUID | None,
    document_id: uuid.UUID | None,
    job_type: str,
    user_id: uuid.UUID | None,
    sync_mode: bool = False,
) -> ProcessingJob:
    # 1. Create the job db record
    job = create_processing_job(db, org_id, project_id, document_id, job_type, user_id)

    # 2. Enqueue or run sync
    if settings.QUEUE_ENABLED and not sync_mode:
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(enqueue_to_redis(job.id))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)
        except RuntimeError:
            # No running loop, run using asyncio.run
            asyncio.run(enqueue_to_redis(job.id))
    else:
        # If QUEUE_ENABLED is False or sync_mode is True, run synchronously
        from app.services.project_service import run_job_sync

        run_job_sync(job.id)

    return job
