import uuid
from typing import Any, ClassVar

from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.job import ProcessingJob
from app.services.project_service import process_job_pipeline_async


async def process_document_task(ctx: Any, job_id_str: str) -> None:
    job_id = uuid.UUID(job_id_str)
    db = SessionLocal()
    try:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id))
        if not job:
            raise ValueError(f"Job {job_id} not found in database")

        from app.core.observability import request_id_var

        token = None
        if job.request_id:
            token = request_id_var.set(job.request_id)

        try:
            await process_job_pipeline_async(db, job)
        finally:
            if token:
                request_id_var.reset(token)
    finally:
        db.close()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [process_document_task]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    job_timeout = settings.JOB_TIMEOUT_SECONDS
    max_jobs = 4
