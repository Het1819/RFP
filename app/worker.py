import asyncio
import logging
import uuid
from typing import Any, ClassVar

from arq.connections import RedisSettings
from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.document import Document
from app.models.job import ProcessingJob
from app.services.ingestion_state import IngestionStatus
from app.services.malware_scan import prepare_scan_attempt, run_scan
from app.services.project_service import process_job_pipeline_async

logger = logging.getLogger(__name__)


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

    Document-locking, org_id resolution, and any SCAN_FAILED -> SCANNING
    re-arming (for a task run that is itself a bounded retry) are
    delegated to `malware_scan.prepare_scan_attempt`, shared with
    `run_scan_sync` so both entry points get identical locking and
    attempt-cap bounding -- see that function's docstring for why both
    the row lock and the attempt-cap re-check matter under arq's
    at-least-once delivery.
    """
    from app.core.database import SessionLocal as _SessionLocal

    document_id = uuid.UUID(document_id_str)
    db = _SessionLocal()
    try:
        org_id = prepare_scan_attempt(db, document_id)
        if org_id is None:
            return

        await asyncio.to_thread(run_scan, db, document_id, org_id=org_id)

        document = db.get(Document, document_id)
        if (
            document is not None
            and document.ingestion_status == IngestionStatus.SCAN_FAILED
            and document.scan_attempt_count < settings.SCAN_MAX_ATTEMPTS
        ):
            from app.core.queue import enqueue_scan_retry

            enqueue_scan_retry(document_id, attempt=document.scan_attempt_count + 1)
    finally:
        db.close()


async def promote_document_task(ctx: Any, document_id_str: str) -> None:
    """A5d Pass 2 worker entry point: run crash-safe clean-storage promotion
    for `document_id_str`. Independent of the legacy ProcessingJob table.
    """
    from app.core.database import SessionLocal as _SessionLocal
    from app.models.document import Document
    from app.models.project import ProposalProject
    from app.services.clean_storage_promotion import (
        PromotionError,
        promote_document,
    )

    document_id = uuid.UUID(document_id_str)
    db = _SessionLocal()
    try:
        doc = (
            db.query(Document)
            .filter(Document.id == document_id)
            .with_for_update()
            .first()
        )
        if not doc:
            return

        project = (
            db.query(ProposalProject)
            .filter(ProposalProject.id == doc.project_id)
            .first()
        )
        if not project:
            return

        org_id = project.organization_id
        user_id = doc.created_by_id

        await asyncio.to_thread(
            promote_document, db, document_id, org_id=org_id, user_id=user_id
        )
    except PromotionError:
        pass
    finally:
        db.close()


async def parse_document_task(ctx: Any, document_id_str: str) -> None:
    """A5e Pass 2 worker entry point: run document parsing orchestration
    and atomic DocumentPage persistence for `document_id_str`.
    """
    document_id = uuid.UUID(document_id_str)
    from app.services.document_parsing import run_parse_pipeline_async

    await run_parse_pipeline_async(document_id)


async def extract_requirements_task(
    ctx: Any, document_id_str: str, organization_id_str: str
) -> None:
    """A5f Pass 2B1 worker entry point: extract requirement candidates.

    The worker owns its own database session, re-derives everything it needs
    from `document_id` and `organization_id`, and builds the extractor from
    trusted settings -- the job payload can influence neither the provider nor
    the prompt.

    This task never creates an authoritative Requirement, never approves a
    candidate, and never calls a review route. Its entire output is PROPOSED
    candidates plus their review tasks; promotion stays behind the human
    review service added in Pass 2A.
    """
    from app.core.database import SessionLocal as _SessionLocal
    from app.services.candidate_extraction import (
        ExtractionServiceError,
        create_requirement_candidates,
    )
    from app.services.requirement_extractor import build_requirement_extractor

    document_id = uuid.UUID(document_id_str)
    organization_id = uuid.UUID(organization_id_str)

    db = _SessionLocal()
    try:
        extractor = build_requirement_extractor()
        create_requirement_candidates(db, document_id, organization_id, extractor)
    except ExtractionServiceError as err:
        # Terminal, already recorded on the run with a fixed code. Retrying a
        # deterministic rejection (input limit, snapshot drift, schema failure)
        # would just burn provider budget reproducing the same outcome; only
        # transient provider faults are retried, and that happens inside the
        # adapter where the distinction is actually visible.
        logger.warning(
            "extract_requirements_task: extraction failed for %s (%s)",
            document_id,
            err.code,
        )
    except Exception:
        logger.exception(
            "extract_requirements_task: unexpected failure for %s", document_id
        )
    finally:
        db.close()


class WorkerSettings:
    functions: ClassVar[list[Any]] = [
        process_document_task,
        scan_document_task,
        promote_document_task,
        parse_document_task,
        extract_requirements_task,
    ]
    redis_settings = RedisSettings.from_dsn(settings.effective_redis_url)
    job_timeout = settings.JOB_TIMEOUT_SECONDS
    max_jobs = 4
