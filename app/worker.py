import asyncio
import uuid
from typing import Any, ClassVar

from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.document import Document
from app.models.job import ProcessingJob
from app.models.project import ProposalProject
from app.services.ingestion_state import IngestionStatus, transition
from app.services.malware_scan import run_scan
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


async def scan_document_task(ctx: Any, document_id_str: str) -> None:
    """A5c Task 6 worker entry point: run one malware/content-policy scan
    attempt for `document_id_str`. Deliberately independent of the legacy
    ProcessingJob table -- no ProcessingJob row is read, written, or
    required for this task to run.

    `clamav_client`'s socket I/O and the PDF-inspector subprocess call
    inside `run_scan` are both blocking/synchronous, so the call is
    wrapped in `asyncio.to_thread(...)` to avoid blocking the arq event
    loop for the duration of a scan.
    """
    from app.core.database import SessionLocal as _SessionLocal

    document_id = uuid.UUID(document_id_str)
    db = _SessionLocal()
    try:
        document = db.get(Document, document_id)
        if document is None:
            return  # deleted/invalid -- nothing to scan, not an error
        project = db.get(ProposalProject, document.project_id)
        if project is None:
            return
        org_id = project.organization_id

        # Re-arm SCAN_FAILED -> SCANNING when this task run is itself a
        # bounded retry: run_scan's own idempotency guard only proceeds
        # when the document is SCANNING.
        if document.ingestion_status == IngestionStatus.SCAN_FAILED:
            transition(
                db,
                document,
                IngestionStatus.SCANNING,
                org_id=org_id,
                user_id=document.created_by_id,
            )

        await asyncio.to_thread(run_scan, db, document_id, org_id=org_id)

        if (
            document.ingestion_status == IngestionStatus.SCAN_FAILED
            and document.scan_attempt_count < settings.SCAN_MAX_ATTEMPTS
        ):
            from app.core.queue import enqueue_scan_retry

            enqueue_scan_retry(document_id, attempt=document.scan_attempt_count + 1)
    finally:
        db.close()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [process_document_task, scan_document_task]
    redis_settings = RedisSettings.from_dsn(settings.effective_redis_url)
    job_timeout = settings.JOB_TIMEOUT_SECONDS
    max_jobs = 4
