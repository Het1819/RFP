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

    redis = await create_pool(RedisSettings.from_dsn(settings.effective_redis_url))
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


# ---------------------------------------------------------------------------
# A5c scan enqueue path (Task 6) -- deliberately parallel to, and
# structurally independent of, create_processing_job/enqueue_to_redis/
# enqueue_job above. A5c must never create a ProcessingJob row: a scan
# attempt is driven entirely by Document.ingestion_status/scan_attempt_count,
# not by a legacy job record.
# ---------------------------------------------------------------------------


async def _enqueue_scan_to_redis(
    document_id: uuid.UUID, *, defer_by: float | None = None
) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    redis = await create_pool(RedisSettings.from_dsn(settings.effective_redis_url))
    await redis.enqueue_job("scan_document_task", str(document_id), _defer_by=defer_by)
    await redis.close()


_ENQUEUE_FAILED_REASON = "SCAN_ENQUEUE_FAILED"


def _handle_scan_enqueue_failure(document_id: uuid.UUID, exc: BaseException) -> None:
    """Recovery path for a failed fire-and-forget scan enqueue.

    If Redis is briefly unavailable when `enqueue_scan_job` fires, the
    document would otherwise be silently stranded in `SCANNING` forever:
    the upload HTTP response already returned 200, no scan attempt was
    ever recorded, and there is no reaper for stale SCANNING rows in this
    phase. Always logs the failure (never silently swallowed); then, on a
    best-effort basis, transitions the document to `SCAN_FAILED` so it
    re-enters the existing bounded-retry machinery
    (`scan_attempt_count`/`SCAN_MAX_ATTEMPTS`) instead of being lost.
    Only touches the document if it is still `SCANNING` -- if something
    else already handled it, this is a no-op.
    """
    logger.error(
        "enqueue_scan_job: failed to enqueue scan for document %s (%s)",
        document_id,
        type(exc).__name__,
    )
    try:
        from app.core.database import SessionLocal
        from app.models.document import Document
        from app.models.project import ProposalProject
        from app.services.ingestion_state import IngestionStatus, transition

        db = SessionLocal()
        try:
            document = db.get(Document, document_id)
            if (
                document is None
                or document.ingestion_status != IngestionStatus.SCANNING
            ):
                return
            project = db.get(ProposalProject, document.project_id)
            if project is None:
                return
            document.scan_attempt_count += 1
            document.scan_status = _ENQUEUE_FAILED_REASON
            transition(
                db,
                document,
                IngestionStatus.SCAN_FAILED,
                org_id=project.organization_id,
                user_id=document.created_by_id,
                reason_code=_ENQUEUE_FAILED_REASON,
                safe_summary=(
                    "The document could not be scanned. It will be "
                    "automatically retried or an operator will review it."
                ),
                audit_detail={"scan_attempt_count": document.scan_attempt_count},
            )
            if document.scan_attempt_count < settings.SCAN_MAX_ATTEMPTS:
                enqueue_scan_retry(document_id, attempt=document.scan_attempt_count + 1)
        finally:
            db.close()
    except Exception:
        # This is already the failure-recovery path -- if it too fails
        # (e.g. the database is also unavailable), the document stays in
        # SCANNING, but the original failure above is still logged, so
        # this is not a silent loss.
        logger.exception(
            "enqueue_scan_job: recovery path itself failed for document %s",
            document_id,
        )


def _on_scan_enqueue_task_done(document_id: uuid.UUID, task: asyncio.Task[Any]) -> None:
    _background_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _handle_scan_enqueue_failure(document_id, exc)


def enqueue_scan_job(document_id: uuid.UUID) -> None:
    """Enqueue one malware/content-policy scan attempt for `document_id`.

    Deliberately bypasses create_processing_job/enqueue_job -- no
    ProcessingJob row is ever created for a scan attempt.
    """
    if not settings.QUEUE_ENABLED:
        # Local/CI/no-queue fallback: run synchronously, mirroring
        # enqueue_job's sync_mode fallback, so tests and QUEUE_ENABLED=false
        # dev environments still exercise the real scan path.
        from app.services.malware_scan import run_scan_sync

        run_scan_sync(document_id)
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_enqueue_scan_to_redis(document_id))
        _background_tasks.add(task)
        task.add_done_callback(lambda t: _on_scan_enqueue_task_done(document_id, t))
    except RuntimeError:
        # No running loop, run using asyncio.run
        try:
            asyncio.run(_enqueue_scan_to_redis(document_id))
        except Exception as exc:
            _handle_scan_enqueue_failure(document_id, exc)


def enqueue_scan_retry(document_id: uuid.UUID, *, attempt: int) -> None:
    """Schedule a bounded, backed-off retry after a SCAN_FAILED outcome.

    Exponential backoff with jitter: delay = min(base * 2**(attempt - 1),
    max), then +/-50% jitter is applied. Callers (run_scan_sync /
    scan_document_task) are responsible for only calling this when
    `attempt <= settings.SCAN_MAX_ATTEMPTS` -- this function does not
    itself re-check the attempt bound, it only computes the delay for the
    attempt it is told to schedule.
    """
    import random

    delay = min(
        settings.SCAN_RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
        settings.SCAN_RETRY_BACKOFF_MAX_SECONDS,
    )
    jittered = delay * (0.5 + random.random())  # 0.5x-1.5x jitter
    if not settings.QUEUE_ENABLED:
        from app.services.malware_scan import run_scan_sync

        # No real queue to defer on in this mode: run the retry attempt
        # immediately/inline rather than after a real backoff delay. This
        # is a deliberate, documented simplification for dev/test/no-queue
        # environments -- see run_scan_sync's docstring.
        run_scan_sync(document_id)
        return
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(_enqueue_scan_to_redis(document_id, defer_by=jittered))
        _background_tasks.add(task)

        def _on_retry_enqueue_done(t: asyncio.Task[Any]) -> None:
            _background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                # This document is already SCAN_FAILED with an existing
                # audit trail from the attempt that led here (unlike
                # enqueue_scan_job's initial-enqueue failure, which starts
                # from a document with zero attempts and zero audit
                # history) -- so a full transition-on-failure recovery
                # isn't attempted here. At minimum, this must never be
                # silently swallowed: it means a scheduled retry never
                # actually got enqueued, so the document will sit in
                # SCAN_FAILED with attempts remaining until an operator
                # notices via this log line. See RUNBOOK.md 5.1.
                logger.error(
                    "enqueue_scan_retry: failed to enqueue retry for "
                    "document %s attempt %d (%s)",
                    document_id,
                    attempt,
                    type(exc).__name__,
                )

        task.add_done_callback(_on_retry_enqueue_done)
    except RuntimeError:
        asyncio.run(_enqueue_scan_to_redis(document_id, defer_by=jittered))
