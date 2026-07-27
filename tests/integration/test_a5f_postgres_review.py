"""PostgreSQL-specific validation for A5f candidate review (Pass 2A).

Pass 1 validated SQLite only. Three of the guarantees this feature depends on
do not exist in SQLite and therefore were never actually tested:

  - the partial unique index on completed extraction runs (SQLite has no
    partial indexes, so Pass 1 skipped creating it entirely);
  - real SELECT ... FOR UPDATE row locking (SQLite serialises writers, so a
    concurrency bug cannot reproduce there);
  - PostgreSQL's NULL handling in a UNIQUE constraint, which is what allows
    many legacy Requirements with a NULL source_candidate_id to coexist.

These tests run the real migration chain against a live PostgreSQL database and
exercise those guarantees directly, including two genuinely concurrent
connections racing to approve the same candidate.

Skipped automatically when no PostgreSQL test service is reachable.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import uuid

import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.models.document import Document, DocumentPage
from app.models.extraction import (
    CANDIDATE_STATUS_APPROVED,
    CANDIDATE_STATUS_PROPOSED,
    CandidateReviewTask,
    RequirementCandidate,
)
from app.models.organization import Organization
from app.models.project import ProposalProject
from app.models.requirement import Requirement
from app.models.user import User
from app.services.candidate_extraction import create_requirement_candidates
from app.services.candidate_review import (
    DECISION_APPROVE,
    CandidateReviewError,
    review_requirement_candidate,
)
from app.services.ingestion_state import IngestionStatus
from app.services.requirement_extractor import FixtureRequirementExtractor

PAGE_TEXT = "The vendor MUST provide 99.9% uptime SLA for all core services."

_PG_URL = os.environ.get(
    "A5F_POSTGRES_TEST_URL",
    "postgresql+psycopg://rfp_user:rfp_password@localhost:5432/rfp_architect",
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _postgres_available() -> bool:
    try:
        engine = create_engine(_PG_URL, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _postgres_available(),
    reason="No PostgreSQL test service reachable for A5f validation",
)

_BASE_ENV = {
    "APP_ENV": "test",
    "AUTH_MODE": "dev",
    "SECRET_KEY": "t" * 48,
    "LOGIN_THROTTLE_SECRET": "t" * 48,
}


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **_BASE_ENV, "DATABASE_URL": database_url}
    env.pop("DB_HOST", None)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ),
    )


@pytest.fixture(scope="module")
def pg_url() -> str:
    """A dedicated scratch database, migrated to head and dropped afterwards."""
    admin = create_engine(_PG_URL, isolation_level="AUTOCOMMIT")
    db_name = f"a5f_test_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))

    target_url = _PG_URL.rsplit("/", 1)[0] + f"/{db_name}"

    result = _run_alembic("upgrade", "head", database_url=target_url)
    assert result.returncode == 0, result.stderr

    yield target_url

    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :n AND pid <> pg_backend_pid()"
            ),
            {"n": db_name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}"'))
    admin.dispose()


@pytest.fixture
def pg_session(pg_url):
    engine = create_engine(pg_url)
    maker = sessionmaker(bind=engine)
    session = maker()
    yield session
    session.close()
    engine.dispose()


def _seed(session: Session, *, can_review=True, content=PAGE_TEXT):
    org = Organization(name=f"Org-{uuid.uuid4().hex[:6]}")
    session.add(org)
    session.flush()

    user = User(
        organization_id=org.id,
        email=f"u{uuid.uuid4().hex[:10]}@test.com",
        hashed_password="x",
        full_name="Reviewer",
        can_review_requirements=can_review,
    )
    session.add(user)
    session.flush()

    project = ProposalProject(
        organization_id=org.id, name="P", client_name="C", created_by_id=user.id
    )
    session.add(project)
    session.flush()

    doc = Document(
        project_id=project.id,
        created_by_id=user.id,
        name="rfp.pdf",
        display_filename="rfp.pdf",
        file_path=f"{uuid.uuid4()}.upload",
        file_type="application/pdf",
        file_size_bytes=1000,
        sha256_digest=_sha256("bytes"),
        ingestion_status=IngestionStatus.COMPLETED,
    )
    session.add(doc)
    session.flush()

    page = DocumentPage(
        document_id=doc.id,
        page_number=1,
        content=content,
        unit_kind="PDF_PAGE",
        source_locator="page_1",
        content_sha256=_sha256(content),
    )
    session.add(page)
    session.commit()

    run = create_requirement_candidates(
        session, doc.id, org.id, FixtureRequirementExtractor()
    )
    candidate = session.scalar(
        select(RequirementCandidate).where(
            RequirementCandidate.extraction_run_id == run.id
        )
    )
    return org, user, project, doc, page, run, candidate


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def test_migration_upgrade_head_creates_expected_schema(pg_url):
    engine = create_engine(pg_url)
    inspector = inspect(engine)

    tables = set(inspector.get_table_names())
    assert {
        "extraction_runs",
        "requirement_candidates",
        "candidate_review_tasks",
    } <= tables

    user_cols = {c["name"]: c for c in inspector.get_columns("users")}
    assert "can_review_requirements" in user_cols
    assert user_cols["can_review_requirements"]["nullable"] is False

    req_cols = {c["name"] for c in inspector.get_columns("requirements")}
    assert "source_candidate_id" in req_cols

    run_cols = {c["name"] for c in inspector.get_columns("extraction_runs")}
    assert {
        "received_candidate_count",
        "accepted_candidate_count",
        "skipped_candidate_count",
        "validation_issue_counts",
        # Pass 2B1 provider usage accounting.
        "provider_call_count",
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
        "duration_ms",
    } <= run_cols

    engine.dispose()


def test_migration_downgrade_and_reupgrade(pg_url):
    """The two Pass 2A migrations must reverse cleanly and reapply."""
    down = _run_alembic("downgrade", "d1e2f3a4b5c6", database_url=pg_url)
    assert down.returncode == 0, down.stderr

    engine = create_engine(pg_url)
    inspector = inspect(engine)
    assert "can_review_requirements" not in {
        c["name"] for c in inspector.get_columns("users")
    }
    assert "source_candidate_id" not in {
        c["name"] for c in inspector.get_columns("requirements")
    }
    engine.dispose()

    up = _run_alembic("upgrade", "head", database_url=pg_url)
    assert up.returncode == 0, up.stderr

    engine = create_engine(pg_url)
    inspector = inspect(engine)
    assert "can_review_requirements" in {
        c["name"] for c in inspector.get_columns("users")
    }
    engine.dispose()


def test_capability_server_default_backfills_false(pg_session):
    """A row inserted without the column must come back false, not NULL."""
    org = Organization(name=f"Org-{uuid.uuid4().hex[:6]}")
    pg_session.add(org)
    pg_session.commit()

    user_id = uuid.uuid4()
    pg_session.execute(
        text(
            "INSERT INTO users (id, organization_id, email, hashed_password, "
            "full_name, is_active, created_at) VALUES (:id, :org, :email, 'x', "
            "'Legacy User', true, now())"
        ),
        {"id": user_id, "org": org.id, "email": f"legacy{uuid.uuid4().hex[:8]}@t.com"},
    )
    pg_session.commit()

    value = pg_session.execute(
        text("SELECT can_review_requirements FROM users WHERE id = :id"),
        {"id": user_id},
    ).scalar_one()
    assert value is False


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_source_candidate_id_is_unique_when_populated(pg_session):
    org, user, project, _doc, _page, _run, candidate = _seed(pg_session)

    review_requirement_candidate(
        pg_session, candidate.id, user.id, org.id, DECISION_APPROVE
    )

    duplicate = Requirement(
        project_id=project.id,
        source_candidate_id=candidate.id,
        original_text="Second requirement for the same candidate",
        status="NOT_STARTED",
    )
    pg_session.add(duplicate)
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


def test_multiple_legacy_requirements_may_have_null_candidate(pg_session):
    """PostgreSQL treats NULLs as distinct in a UNIQUE constraint."""
    org = Organization(name=f"Org-{uuid.uuid4().hex[:6]}")
    pg_session.add(org)
    pg_session.flush()
    user = User(
        organization_id=org.id,
        email=f"u{uuid.uuid4().hex[:10]}@test.com",
        hashed_password="x",
        full_name="U",
    )
    pg_session.add(user)
    pg_session.flush()
    project = ProposalProject(
        organization_id=org.id, name="P", client_name="C", created_by_id=user.id
    )
    pg_session.add(project)
    pg_session.flush()

    for i in range(3):
        pg_session.add(
            Requirement(
                project_id=project.id,
                original_text=f"Legacy requirement {i}",
                status="NOT_STARTED",
            )
        )
    pg_session.commit()

    rows = pg_session.scalars(
        select(Requirement).where(
            Requirement.project_id == project.id,
            Requirement.source_candidate_id.is_(None),
        )
    ).all()
    assert len(rows) == 3


def test_candidate_review_task_is_unique_per_candidate(pg_session):
    _org, _user, project, _doc, _page, run, candidate = _seed(pg_session)

    duplicate = CandidateReviewTask(
        organization_id=candidate.organization_id,
        project_id=project.id,
        candidate_id=candidate.id,
        extraction_run_id=run.id,
        source_locator="page_1",
    )
    pg_session.add(duplicate)
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


def test_completed_run_partial_unique_index_exists(pg_session):
    """Pass 1's idempotency index is PostgreSQL-only and was never verified."""
    row = pg_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE indexname = 'uq_extraction_runs_completed_snapshot'"
        )
    ).fetchone()
    assert row is not None, "partial unique index missing on extraction_runs"
    indexdef = row[0]
    assert "UNIQUE" in indexdef
    assert "COMPLETED" in indexdef


def test_duplicate_completed_run_blocked_by_index(pg_session):
    from app.models.extraction import EXTRACTION_STATUS_COMPLETED, ExtractionRun

    org, _user, project, doc, _page, run, _candidate = _seed(pg_session)

    clone = ExtractionRun(
        organization_id=org.id,
        project_id=project.id,
        document_id=doc.id,
        status=EXTRACTION_STATUS_COMPLETED,
        extraction_attempt_id=str(uuid.uuid4()),
        input_snapshot_sha256=run.input_snapshot_sha256,
        page_count=run.page_count,
        extraction_schema_version=run.extraction_schema_version,
        prompt_version=run.prompt_version,
    )
    pg_session.add(clone)
    with pytest.raises(IntegrityError):
        pg_session.commit()
    pg_session.rollback()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_select_for_update_blocks_a_second_reader(pg_url, pg_session):
    """Prove the row lock is real: a second connection must wait on it."""
    _org, _user, _project, _doc, _page, _run, candidate = _seed(pg_session)
    candidate_id = candidate.id
    pg_session.commit()

    engine_a = create_engine(pg_url)
    engine_b = create_engine(pg_url)
    session_a = sessionmaker(bind=engine_a)()
    session_b = sessionmaker(bind=engine_b)()

    try:
        session_a.execute(
            select(RequirementCandidate)
            .where(RequirementCandidate.id == candidate_id)
            .with_for_update()
        ).scalar_one()

        session_b.execute(text("SET lock_timeout = '750ms'"))
        with pytest.raises(OperationalError):
            session_b.execute(
                select(RequirementCandidate)
                .where(RequirementCandidate.id == candidate_id)
                .with_for_update()
            ).scalar_one()
    finally:
        session_a.rollback()
        session_b.rollback()
        session_a.close()
        session_b.close()
        engine_a.dispose()
        engine_b.dispose()


def test_concurrent_approvals_create_exactly_one_requirement(pg_url, pg_session):
    """Two reviewers racing on the same candidate must yield one Requirement."""
    org, user, _project, _doc, _page, _run, candidate = _seed(pg_session)
    candidate_id = candidate.id
    org_id = org.id
    user_id = user.id
    pg_session.commit()

    results: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def _approve() -> None:
        engine = create_engine(pg_url)
        session = sessionmaker(bind=engine)()
        try:
            barrier.wait(timeout=10)
            result = review_requirement_candidate(
                session, candidate_id, user_id, org_id, DECISION_APPROVE
            )
            results.append(result.result_code)
        except CandidateReviewError as err:
            results.append(err.code)
        except Exception as err:  # pragma: no cover - diagnostic only
            errors.append(err)
        finally:
            session.close()
            engine.dispose()

    threads = [threading.Thread(target=_approve) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, f"unexpected errors: {errors}"
    assert len(results) == 2

    verify_engine = create_engine(pg_url)
    verify = sessionmaker(bind=verify_engine)()
    try:
        requirements = verify.scalars(
            select(Requirement).where(Requirement.source_candidate_id == candidate_id)
        ).all()
        # The whole point: exactly one authoritative Requirement, whichever
        # reviewer won the race.
        assert len(requirements) == 1

        settled = verify.get(RequirementCandidate, candidate_id)
        assert settled.candidate_status == CANDIDATE_STATUS_APPROVED
    finally:
        verify.close()
        verify_engine.dispose()

    # Exactly one thread performed the decision; the other saw a settled state.
    assert "REVIEW_OK" in results


def test_failed_review_leaves_candidate_proposed_on_postgres(pg_session):
    org, user, _project, _doc, page, _run, candidate = _seed(pg_session)

    page.content = "Rewritten page content after a reparse."
    page.content_sha256 = _sha256(page.content)
    pg_session.commit()

    with pytest.raises(CandidateReviewError):
        review_requirement_candidate(
            pg_session, candidate.id, user.id, org.id, DECISION_APPROVE
        )

    pg_session.rollback()
    refreshed = pg_session.get(RequirementCandidate, candidate.id)
    assert refreshed.candidate_status == CANDIDATE_STATUS_PROPOSED
    assert (
        pg_session.scalars(
            select(Requirement).where(Requirement.source_candidate_id == candidate.id)
        ).all()
        == []
    )
