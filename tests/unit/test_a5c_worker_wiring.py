"""Tests for A5c Task 6: wiring the scanner orchestration
(app.services.malware_scan.run_scan) into the async job system.

Covers: enqueue_scan_job's QUEUE_ENABLED=false sync fallback never
touches Redis; enqueue_scan_retry's backoff math (monotonic, jittered,
capped); app.worker.scan_document_task invokes run_scan for a real
document and no-ops for a missing document id; no ProcessingJob row is
ever created by any of these paths; prepare_scan_attempt (shared by both
run_scan_sync and scan_document_task) never re-arms an already-exhausted
SCAN_FAILED document, and acquires the same row lock run_scan itself uses
before re-arming.
"""

import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import app.worker as worker_mod
from app.core.config import settings
from app.core.queue import enqueue_scan_job, enqueue_scan_retry
from app.models.document import Document
from app.models.job import ProcessingJob
from app.services import malware_scan
from app.services.clamav_client import ScanOutcome, ScanResult, VersionInfo
from app.services.ingestion_state import IngestionStatus
from app.services.pdf_content_policy import PdfPolicyResult


def _make_document(
    db,
    project,
    user,
    *,
    ingestion_status: str = IngestionStatus.SCANNING,
    scan_attempt_count: int = 0,
) -> Document:
    doc = Document(
        project_id=project.id,
        name="test.pdf",
        file_path="/nonexistent/quarantine/does-not-matter.pdf",
        file_type="application/pdf",
        created_by_id=user.id,
        ingestion_status=ingestion_status,
        sha256_digest="deadbeef",
        detected_content_type="application/pdf",
        scan_attempt_count=scan_attempt_count,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


def _fresh_version_info() -> VersionInfo:
    return VersionInfo(
        raw="ClamAV 1.4.5/28058/Sun Jul 12 06:25:26 2026",
        engine_version="1.4.5",
        signature_version="28058",
        signature_timestamp=datetime.now(UTC) - timedelta(hours=1),
    )


class TestEnqueueScanJobQueueDisabled:
    def test_calls_run_scan_sync_and_never_touches_redis(
        self, db, org_project_user, monkeypatch
    ) -> None:
        _org, project, user = org_project_user
        monkeypatch.setattr(settings, "QUEUE_ENABLED", False)
        doc = _make_document(db, project, user)

        redis_calls: list = []

        async def _fake_redis_enqueue(*args: object, **kwargs: object) -> None:
            redis_calls.append((args, kwargs))

        monkeypatch.setattr(
            "app.core.queue._enqueue_scan_to_redis", _fake_redis_enqueue
        )

        sync_calls: list = []
        monkeypatch.setattr(
            "app.services.malware_scan.run_scan_sync",
            lambda document_id: sync_calls.append(document_id),
        )

        enqueue_scan_job(doc.id)

        assert sync_calls == [doc.id]
        assert redis_calls == []

    def test_no_processing_job_created(self, db, org_project_user, monkeypatch) -> None:
        _org, project, user = org_project_user
        monkeypatch.setattr(settings, "QUEUE_ENABLED", False)
        doc = _make_document(db, project, user)

        # Exercise the real run_scan_sync path (not mocked), with clamav
        # and content-policy inspection monkeypatched to a clean verdict,
        # to confirm the full sync fallback never creates a ProcessingJob.
        monkeypatch.setattr(
            malware_scan.clamav_client, "get_version_info", _fresh_version_info
        )
        monkeypatch.setattr(
            malware_scan.clamav_client,
            "scan_stream",
            lambda *a, **kw: ScanResult(
                outcome=ScanOutcome.CLEAN,
                signature_name=None,
                engine_version="1.4.5",
                signature_version="28058",
            ),
        )
        monkeypatch.setattr(
            malware_scan.pdf_content_policy,
            "check_pdf_content_policy",
            lambda *a, **kw: PdfPolicyResult(
                passed=True, reason_code=None, policy_version="v1"
            ),
        )
        # run_scan re-resolves the quarantine path and digest; force the
        # digest-drift guard to pass without a real file on disk.
        monkeypatch.setattr(
            malware_scan,
            "_resolve_and_verify_quarantine_path",
            lambda document: Path("/dev/null"),
        )

        enqueue_scan_job(doc.id)

        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.CLEAN_PENDING_PROMOTION
        jobs = db.query(ProcessingJob).all()
        assert jobs == []


class TestEnqueueScanRetryBackoff:
    def test_delay_is_monotonically_increasing_and_capped(self, monkeypatch) -> None:
        # QUEUE_ENABLED=True with a stubbed _enqueue_scan_to_redis so we
        # can inspect the computed defer_by without a real Redis/arq pool.
        monkeypatch.setattr(settings, "QUEUE_ENABLED", True)
        monkeypatch.setattr(settings, "SCAN_RETRY_BACKOFF_BASE_SECONDS", 5)
        monkeypatch.setattr(settings, "SCAN_RETRY_BACKOFF_MAX_SECONDS", 300)

        seen_defers: list[float] = []

        async def _fake_redis_enqueue(document_id, *, defer_by=None) -> None:
            seen_defers.append(defer_by)

        monkeypatch.setattr(
            "app.core.queue._enqueue_scan_to_redis", _fake_redis_enqueue
        )

        doc_id = uuid.uuid4()
        # Run enough attempts to see both the exponential growth and the
        # cap kick in. base=5: uncapped delay at attempt N is 5 * 2**(N-1).
        for attempt in range(1, 9):
            enqueue_scan_retry(doc_id, attempt=attempt)

        assert len(seen_defers) == 8
        for attempt, jittered in zip(range(1, 9), seen_defers, strict=True):
            uncapped = 5 * (2 ** (attempt - 1))
            capped = min(uncapped, 300)
            # jitter is 0.5x-1.5x of the capped delay
            assert capped * 0.5 <= jittered <= capped * 1.5

        # The capped, pre-jitter delay is monotonically non-decreasing.
        capped_delays = [min(5 * (2 ** (a - 1)), 300) for a in range(1, 9)]
        assert capped_delays == sorted(capped_delays)
        # And the cap is actually reached for large attempts.
        assert capped_delays[-1] == 300

    def test_queue_disabled_runs_inline_immediately(
        self, db, org_project_user, monkeypatch
    ) -> None:
        _org, project, user = org_project_user
        monkeypatch.setattr(settings, "QUEUE_ENABLED", False)
        doc = _make_document(
            db, project, user, ingestion_status=IngestionStatus.SCAN_FAILED
        )

        sync_calls: list = []
        monkeypatch.setattr(
            "app.services.malware_scan.run_scan_sync",
            lambda document_id: sync_calls.append(document_id),
        )
        redis_calls: list = []
        monkeypatch.setattr(
            "app.core.queue._enqueue_scan_to_redis",
            lambda *a, **kw: redis_calls.append((a, kw)),
        )

        enqueue_scan_retry(doc.id, attempt=1)

        assert sync_calls == [doc.id]
        assert redis_calls == []


class TestScanDocumentTask:
    def test_invokes_run_scan_off_event_loop_thread(
        self, db, org_project_user, monkeypatch
    ) -> None:
        org, project, user = org_project_user
        doc = _make_document(db, project, user)

        main_thread = threading.current_thread()
        observed_threads: list[threading.Thread] = []
        observed_args: list[tuple] = []

        def _fake_run_scan(db_arg, document_id, *, org_id):
            observed_threads.append(threading.current_thread())
            observed_args.append((document_id, org_id))

        monkeypatch.setattr(worker_mod, "run_scan", _fake_run_scan)

        asyncio.run(worker_mod.scan_document_task(None, str(doc.id)))

        assert observed_args == [(doc.id, org.id)]
        assert observed_threads[0] is not main_thread

    def test_missing_document_is_noop(self, db, org_project_user, monkeypatch) -> None:
        called = []
        monkeypatch.setattr(
            worker_mod, "run_scan", lambda *a, **kw: called.append(True)
        )

        asyncio.run(worker_mod.scan_document_task(None, str(uuid.uuid4())))

        assert called == []

    def test_no_processing_job_created(self, db, org_project_user, monkeypatch) -> None:
        _org, project, user = org_project_user
        doc = _make_document(db, project, user)
        monkeypatch.setattr(worker_mod, "run_scan", lambda *a, **kw: None)

        asyncio.run(worker_mod.scan_document_task(None, str(doc.id)))

        jobs = db.query(ProcessingJob).all()
        assert jobs == []

    def test_rearms_scan_failed_document_before_retry(
        self, db, org_project_user, monkeypatch
    ) -> None:
        _org, project, user = org_project_user
        doc = _make_document(
            db, project, user, ingestion_status=IngestionStatus.SCAN_FAILED
        )

        seen_status_at_call: list[str] = []

        def _fake_run_scan(db_arg, document_id, *, org_id):
            document = db_arg.get(Document, document_id)
            seen_status_at_call.append(document.ingestion_status)

        monkeypatch.setattr(worker_mod, "run_scan", _fake_run_scan)

        asyncio.run(worker_mod.scan_document_task(None, str(doc.id)))

        assert seen_status_at_call == [IngestionStatus.SCANNING]


class TestPrepareScanAttemptExhaustionGuard:
    """Regression coverage for review finding 1: prepare_scan_attempt must
    re-check scan_attempt_count against SCAN_MAX_ATTEMPTS at the moment it
    is about to re-arm, not rely solely on the caller's post-run_scan
    check -- run_scan itself unconditionally increments
    scan_attempt_count on every call, so under arq's at-least-once
    delivery a stray/duplicate retry invocation for an already-exhausted
    document must not re-arm it for one more attempt."""

    def test_prepare_scan_attempt_returns_none_and_leaves_terminal(
        self, db, org_project_user, monkeypatch
    ) -> None:
        _org, project, user = org_project_user
        monkeypatch.setattr(settings, "SCAN_MAX_ATTEMPTS", 3)
        doc = _make_document(
            db,
            project,
            user,
            ingestion_status=IngestionStatus.SCAN_FAILED,
            scan_attempt_count=3,
        )

        org_id = malware_scan.prepare_scan_attempt(db, doc.id)

        assert org_id is None
        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.SCAN_FAILED
        assert doc.scan_attempt_count == 3

    def test_run_scan_sync_does_not_call_run_scan_when_exhausted(
        self, db, org_project_user, monkeypatch
    ) -> None:
        _org, project, user = org_project_user
        monkeypatch.setattr(settings, "SCAN_MAX_ATTEMPTS", 3)
        doc = _make_document(
            db,
            project,
            user,
            ingestion_status=IngestionStatus.SCAN_FAILED,
            scan_attempt_count=3,
        )

        run_scan_calls: list = []
        monkeypatch.setattr(
            malware_scan, "run_scan", lambda *a, **kw: run_scan_calls.append(True)
        )
        retry_calls: list = []
        monkeypatch.setattr(
            "app.core.queue.enqueue_scan_retry",
            lambda *a, **kw: retry_calls.append((a, kw)),
        )

        malware_scan.run_scan_sync(doc.id)

        assert run_scan_calls == []
        assert retry_calls == []
        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.SCAN_FAILED
        assert doc.scan_attempt_count == 3

    def test_scan_document_task_does_not_call_run_scan_when_exhausted(
        self, db, org_project_user, monkeypatch
    ) -> None:
        _org, project, user = org_project_user
        monkeypatch.setattr(settings, "SCAN_MAX_ATTEMPTS", 3)
        doc = _make_document(
            db,
            project,
            user,
            ingestion_status=IngestionStatus.SCAN_FAILED,
            scan_attempt_count=3,
        )

        run_scan_calls: list = []
        monkeypatch.setattr(
            worker_mod, "run_scan", lambda *a, **kw: run_scan_calls.append(True)
        )

        asyncio.run(worker_mod.scan_document_task(None, str(doc.id)))

        assert run_scan_calls == []
        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.SCAN_FAILED
        assert doc.scan_attempt_count == 3

    def test_still_rearms_when_attempts_remain_below_cap(
        self, db, org_project_user, monkeypatch
    ) -> None:
        """Sanity check alongside the exhaustion guard above: a document
        one attempt short of the cap is still eligible and IS re-armed."""
        _org, project, user = org_project_user
        monkeypatch.setattr(settings, "SCAN_MAX_ATTEMPTS", 3)
        doc = _make_document(
            db,
            project,
            user,
            ingestion_status=IngestionStatus.SCAN_FAILED,
            scan_attempt_count=2,
        )

        org_id = malware_scan.prepare_scan_attempt(db, doc.id)

        assert org_id is not None
        db.refresh(doc)
        assert doc.ingestion_status == IngestionStatus.SCANNING


class TestPrepareScanAttemptRowLock:
    """Regression coverage for review finding 2: the re-arm read-modify-
    write must go through the same row lock run_scan itself uses
    internally (_lock_document's PostgreSQL-only `.with_for_update()`),
    not an unlocked db.get(), to avoid two racing callers each
    successfully committing a duplicate SCAN_FAILED -> SCANNING
    transition/AuditEvent."""

    def test_acquires_for_update_lock_on_postgresql_dialect(
        self, db, org_project_user, monkeypatch
    ) -> None:
        _org, project, user = org_project_user
        doc = _make_document(
            db, project, user, ingestion_status=IngestionStatus.SCAN_FAILED
        )

        # SQLite (the test engine) has no real row-level FOR UPDATE
        # semantics; force the PostgreSQL-only locking branch, matching
        # the pattern used by test_a5b_quarantine_upload.py's
        # TestRfpUploadRowLock, so the lock statement can still be
        # inspected even though the test DB is SQLite.
        assert db.bind is not None
        monkeypatch.setattr(db.bind.dialect, "name", "postgresql")

        executed_statements: list = []
        original_execute = db.execute

        def _spy_execute(statement, *args, **kwargs):
            executed_statements.append(statement)
            return original_execute(statement, *args, **kwargs)

        monkeypatch.setattr(db, "execute", _spy_execute)

        malware_scan.prepare_scan_attempt(db, doc.id)

        lock_statements = [
            s
            for s in executed_statements
            if "FOR UPDATE" in str(s) and Document.__tablename__ in str(s)
        ]
        assert lock_statements, (
            "expected a SELECT ... FOR UPDATE against the documents table "
            "before the SCAN_FAILED -> SCANNING re-arm"
        )
