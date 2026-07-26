"""Tests for the A5c Alembic migration (a969356849bf).

This migration recreates `ck_documents_ingestion_status_valid` to accept
SCAN_FAILED / CLEAN_PENDING_PROMOTION and reject the retired
SCAN_RETRY_PENDING, and adds four new documents columns
(scan_started_at, scan_attempt_count, content_policy_version,
scan_digest_snapshot).

`alembic/env.py` binds `run_migrations_online()` to the process-global
`app.core.database.engine`, which is instantiated once at import time
from `settings.effective_database_url`. That means the only reliable
way to point a real `alembic upgrade`/`downgrade` invocation at a
throwaway database is a subprocess with `DATABASE_URL` overridden in
its environment - an in-process `alembic.command.upgrade()` call in
this same pytest process would still hit the already-imported engine
bound to the suite's own DATABASE_URL (sqlite:///:memory:), not a
scratch database.

Running `alembic upgrade head` from a *blank* database against this
repo's full migration history does not work on SQLite at all - an
earlier, unrelated migration creates `audit_events.details` as a raw
`postgresql.JSONB()` column (the ORM model itself uses a portable
JSON/JSONB variant, but the historical migration DDL does not), which
SQLAlchemy's SQLite DDL compiler rejects with `CompileError: ... can't
render element of type JSONB`. That is a pre-existing gap unrelated to
this migration; it is not something A5c can or should fix. To isolate
testing to *this* migration, each test seeds a throwaway database with
a hand-written `documents` table (and `alembic_version` row) that
mirrors the real schema as of the immediately-prior head (04ffd9fbcedb)
exactly, then runs `alembic upgrade`/`downgrade` starting from that
point - so only the new revision's own operations ever execute.
"""

import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable

_BASE_ENV = {
    "APP_ENV": "test",
    "AUTH_MODE": "dev",
    "SESSION_SECRET_KEY": (
        "test-session-secret-key-for-ci-only-do-not-use-in-production-12345"
    ),
    "APP_SECRET_KEY": (
        "test-app-secret-key-for-ci-only-do-not-use-in-production-12345"
    ),
    "REDIS_URL": "redis://localhost:6379/0",
    "LLM_PROVIDER": "fake",
    "QUEUE_ENABLED": "false",
}

# Schema as of revision 04ffd9fbcedb (the immediately-prior head), for the
# subset of columns relevant to this migration and its CHECK constraint.
_OLD_INGESTION_STATUSES_SQL = (
    "'QUARANTINED','VALIDATING','SCANNING','SCAN_RETRY_PENDING',"
    "'REJECTED_TYPE','REJECTED_MALWARE','REJECTED_CONTENT_POLICY','CLEAN',"
    "'PARSING','PARSE_FAILED','COMPLETED','LEGACY_UNVERIFIED'"
)

_CREATE_DOCUMENTS_SQL = f"""
CREATE TABLE documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    doc_role TEXT NOT NULL DEFAULT 'rfp',
    content TEXT,
    processing_status TEXT NOT NULL DEFAULT 'pending',
    processing_error TEXT,
    owner_name TEXT,
    tags TEXT,
    approval_status TEXT NOT NULL DEFAULT 'PENDING',
    version TEXT DEFAULT '1.0',
    review_date TIMESTAMP,
    created_by_id TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    ingestion_status VARCHAR(30) NOT NULL DEFAULT 'LEGACY_UNVERIFIED',
    display_filename VARCHAR(255),
    detected_content_type VARCHAR(255),
    sha256_digest VARCHAR(64),
    file_size_bytes BIGINT,
    quarantined_at TIMESTAMP,
    scan_status VARCHAR(30),
    scan_engine_version VARCHAR(100),
    scan_signature_version VARCHAR(100),
    scan_completed_at TIMESTAMP,
    content_policy_status VARCHAR(30),
    parser_version VARCHAR(100),
    parser_completed_at TIMESTAMP,
    rejection_reason_code VARCHAR(100),
    operator_failure_summary TEXT,
    CONSTRAINT ck_documents_ingestion_status_valid
        CHECK (ingestion_status IN ({_OLD_INGESTION_STATUSES_SQL}))
);
"""

_NEW_HEAD_REVISION = "a969356849bf"
_OLD_HEAD_REVISION = "04ffd9fbcedb"


def _seed_pre_a5c_sqlite_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_CREATE_DOCUMENTS_SQL)
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            (_OLD_HEAD_REVISION,),
        )
        conn.commit()
    finally:
        conn.close()


def _run_alembic(*args: str, database_url: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **_BASE_ENV, "DATABASE_URL": database_url}
    return subprocess.run(
        [PYTHON, "-m", "alembic", *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _insert_document(conn: sqlite3.Connection, ingestion_status: str) -> None:
    now = "2026-07-26T00:00:00"
    conn.execute(
        """
        INSERT INTO documents (
            id, project_id, name, file_path, file_type, doc_role,
            processing_status, approval_status, created_by_id,
            created_at, updated_at, ingestion_status
        ) VALUES (?, ?, ?, ?, ?, 'rfp', 'pending', 'PENDING', ?, ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            "test.pdf",
            "/data/quarantine/x.pdf",
            "application/pdf",
            str(uuid.uuid4()),
            now,
            now,
            ingestion_status,
        ),
    )
    conn.commit()


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Iterator[Path]:
    db_path = tmp_path / "a5c_migration_test.db"
    _seed_pre_a5c_sqlite_db(db_path)
    yield db_path


class TestSqliteCheckConstraint:
    """SQLite enforces CHECK constraints (has since SQLite 3.x); confirmed
    empirically below rather than assumed."""

    def test_upgrade_adds_columns_and_new_check_constraint(
        self, sqlite_db_path: Path
    ) -> None:
        url = f"sqlite:///{sqlite_db_path}"
        result = _run_alembic("upgrade", "head", database_url=url)
        assert result.returncode == 0, result.stderr

        conn = sqlite3.connect(str(sqlite_db_path))
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
            for new_col in (
                "scan_started_at",
                "scan_attempt_count",
                "content_policy_version",
                "scan_digest_snapshot",
            ):
                assert new_col in cols

            # New statuses accepted.
            _insert_document(conn, "SCAN_FAILED")
            _insert_document(conn, "CLEAN_PENDING_PROMOTION")

            # scan_attempt_count defaults to 0 for rows that omit it.
            row = conn.execute(
                "SELECT scan_attempt_count FROM documents "
                "WHERE ingestion_status = 'SCAN_FAILED'"
            ).fetchone()
            assert row[0] == 0

            # Retired status rejected.
            with pytest.raises(sqlite3.IntegrityError):
                _insert_document(conn, "SCAN_RETRY_PENDING")
        finally:
            conn.close()

    def test_up_down_up_round_trips(self, sqlite_db_path: Path) -> None:
        url = f"sqlite:///{sqlite_db_path}"

        up1 = _run_alembic("upgrade", "head", database_url=url)
        assert up1.returncode == 0, up1.stderr

        down = _run_alembic("downgrade", "-1", database_url=url)
        assert down.returncode == 0, down.stderr

        conn = sqlite3.connect(str(sqlite_db_path))
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
            for new_col in (
                "scan_started_at",
                "scan_attempt_count",
                "content_policy_version",
                "scan_digest_snapshot",
            ):
                assert new_col not in cols

            # Old status accepted again after downgrade.
            _insert_document(conn, "SCAN_RETRY_PENDING")
            # New-only status rejected again after downgrade.
            with pytest.raises(sqlite3.IntegrityError):
                _insert_document(conn, "SCAN_FAILED")

            # Re-upgrading recreates the table (SQLite batch mode) and
            # revalidates every existing row against the new CHECK
            # constraint - clean up the SCAN_RETRY_PENDING row inserted
            # above first, matching downgrade()'s documented assumption
            # that no row is left in a status the new constraint rejects.
            conn.execute(
                "DELETE FROM documents WHERE ingestion_status = 'SCAN_RETRY_PENDING'"
            )
            conn.commit()
        finally:
            conn.close()

        up2 = _run_alembic("upgrade", "head", database_url=url)
        assert up2.returncode == 0, up2.stderr

        conn = sqlite3.connect(str(sqlite_db_path))
        try:
            cols = {row[1] for row in conn.execute("PRAGMA table_info(documents)")}
            assert "scan_started_at" in cols
            _insert_document(conn, "CLEAN_PENDING_PROMOTION")
        finally:
            conn.close()


def _postgres_available() -> str | None:
    """Return a usable PostgreSQL DATABASE_URL if compose.yml's postgres
    service is reachable locally, else None (skips the Postgres tests)."""
    url = os.environ.get(
        "A5C_TEST_POSTGRES_URL",
        "postgresql+psycopg://rfp_user:rfp_password@localhost:5432/"
        "rfp_architect_a5c_migration_test",
    )
    try:
        import psycopg
    except ImportError:
        return None

    admin_url = url.rsplit("/", 1)[0] + "/rfp_architect"
    try:
        conn = psycopg.connect(
            admin_url.replace("postgresql+psycopg://", "postgresql://"),
            connect_timeout=2,
        )
        conn.close()
    except Exception:
        return None
    return url


@pytest.mark.skipif(
    shutil.which("docker") is None and _postgres_available() is None,
    reason="PostgreSQL not reachable locally (compose.yml postgres service)",
)
class TestPostgresCheckConstraint:
    """Real PostgreSQL up/down/up validation, matching A5a/A5b convention.

    Skipped automatically if the local `compose.yml` postgres service
    (see `docker compose -f compose.yml up -d postgres`) is not reachable.
    """

    @pytest.fixture(autouse=True)
    def _require_postgres(self) -> Iterator[None]:
        url = _postgres_available()
        if url is None:
            pytest.skip("PostgreSQL not reachable locally")
        self.database_url = url

        import psycopg

        admin_url = "postgresql://rfp_user:rfp_password@localhost:5432/rfp_architect"
        db_name = url.rsplit("/", 1)[1]
        conn = psycopg.connect(admin_url, autocommit=True)
        try:
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
            conn.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            conn.close()

        yield

        conn = psycopg.connect(admin_url, autocommit=True)
        try:
            conn.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            conn.close()

    def _seed_pre_a5c_postgres_schema(self) -> None:
        import psycopg

        conn = psycopg.connect(
            self.database_url.replace("postgresql+psycopg://", "postgresql://")
        )
        try:
            conn.execute(
                f"""
                CREATE TABLE documents (
                    id UUID PRIMARY KEY,
                    project_id UUID NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(512) NOT NULL,
                    file_type VARCHAR(255) NOT NULL,
                    doc_role VARCHAR(50) NOT NULL DEFAULT 'rfp',
                    content TEXT,
                    processing_status VARCHAR(50) NOT NULL DEFAULT 'pending',
                    processing_error TEXT,
                    owner_name VARCHAR(255),
                    tags TEXT,
                    approval_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
                    version VARCHAR(50) DEFAULT '1.0',
                    review_date TIMESTAMPTZ,
                    created_by_id UUID NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    ingestion_status VARCHAR(30) NOT NULL
                        DEFAULT 'LEGACY_UNVERIFIED',
                    display_filename VARCHAR(255),
                    detected_content_type VARCHAR(255),
                    sha256_digest VARCHAR(64),
                    file_size_bytes BIGINT,
                    quarantined_at TIMESTAMPTZ,
                    scan_status VARCHAR(30),
                    scan_engine_version VARCHAR(100),
                    scan_signature_version VARCHAR(100),
                    scan_completed_at TIMESTAMPTZ,
                    content_policy_status VARCHAR(30),
                    parser_version VARCHAR(100),
                    parser_completed_at TIMESTAMPTZ,
                    rejection_reason_code VARCHAR(100),
                    operator_failure_summary TEXT,
                    CONSTRAINT ck_documents_ingestion_status_valid
                        CHECK (ingestion_status IN ({_OLD_INGESTION_STATUSES_SQL}))
                );
                """
            )
            conn.execute(
                "CREATE TABLE alembic_version "
                "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
            conn.execute(
                "INSERT INTO alembic_version (version_num) VALUES (%s)",
                (_OLD_HEAD_REVISION,),
            )
            conn.commit()
        finally:
            conn.close()

    def test_postgres_up_down_up(self) -> None:
        import psycopg

        self._seed_pre_a5c_postgres_schema()

        up1 = _run_alembic("upgrade", "head", database_url=self.database_url)
        assert up1.returncode == 0, up1.stderr

        conn = psycopg.connect(
            self.database_url.replace("postgresql+psycopg://", "postgresql://")
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO documents (id, project_id, name, file_path, "
                    "file_type, created_by_id, created_at, updated_at, "
                    "ingestion_status) VALUES (gen_random_uuid(), "
                    "gen_random_uuid(), 'test.pdf', '/x.pdf', "
                    "'application/pdf', gen_random_uuid(), now(), now(), "
                    "'SCAN_FAILED')"
                )
                cur.execute(
                    "INSERT INTO documents (id, project_id, name, file_path, "
                    "file_type, created_by_id, created_at, updated_at, "
                    "ingestion_status) VALUES (gen_random_uuid(), "
                    "gen_random_uuid(), 'test.pdf', '/x.pdf', "
                    "'application/pdf', gen_random_uuid(), now(), now(), "
                    "'CLEAN_PENDING_PROMOTION')"
                )
            conn.commit()

            with pytest.raises(Exception):  # noqa: B017 - psycopg IntegrityError
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO documents (id, project_id, name, "
                        "file_path, file_type, created_by_id, created_at, "
                        "updated_at, ingestion_status) VALUES "
                        "(gen_random_uuid(), gen_random_uuid(), 'test.pdf', "
                        "'/x.pdf', 'application/pdf', gen_random_uuid(), "
                        "now(), now(), 'SCAN_RETRY_PENDING')"
                    )
                conn.commit()
            conn.rollback()

            # downgrade() is a schema-only reversal that assumes no row
            # is in the new-only status set (see downgrade()'s docstring)
            # - clean up the rows this test inserted before downgrading.
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM documents WHERE ingestion_status IN "
                    "('SCAN_FAILED', 'CLEAN_PENDING_PROMOTION')"
                )
            conn.commit()
        finally:
            conn.close()

        down = _run_alembic("downgrade", "-1", database_url=self.database_url)
        assert down.returncode == 0, down.stderr

        up2 = _run_alembic("upgrade", "head", database_url=self.database_url)
        assert up2.returncode == 0, up2.stderr
