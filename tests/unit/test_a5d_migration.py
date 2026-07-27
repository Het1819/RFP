"""Tests for the A5d Alembic migration (b82741d7293a).

This migration updates `ck_documents_ingestion_status_valid` to accept
PROMOTING and PROMOTION_FAILED, and adds promotion metadata columns
(promotion_started_at, promotion_completed_at, promotion_attempt_count,
clean_storage_identifier, cleanup_pending).
"""

import os
import sqlite3
import subprocess
import sys
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

_A5C_INGESTION_STATUSES_SQL = (
    "'QUARANTINED','VALIDATING','SCANNING','REJECTED_TYPE',"
    "'REJECTED_MALWARE','REJECTED_CONTENT_POLICY','SCAN_FAILED',"
    "'CLEAN_PENDING_PROMOTION','CLEAN','PARSING','PARSE_FAILED',"
    "'COMPLETED','LEGACY_UNVERIFIED'"
)

_CREATE_DOCUMENTS_A5C_SQL = f"""
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
    scan_started_at TIMESTAMP,
    scan_attempt_count INTEGER NOT NULL DEFAULT 0,
    content_policy_status VARCHAR(30),
    content_policy_version VARCHAR(50),
    scan_digest_snapshot VARCHAR(64),
    parser_version VARCHAR(100),
    parser_completed_at TIMESTAMP,
    rejection_reason_code VARCHAR(100),
    operator_failure_summary TEXT,
    CONSTRAINT ck_documents_ingestion_status_valid
        CHECK (ingestion_status IN ({_A5C_INGESTION_STATUSES_SQL}))
);
"""

_NEW_HEAD_REVISION = "b82741d7293a"
_OLD_HEAD_REVISION = "a969356849bf"


def _seed_pre_a5d_sqlite_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(_CREATE_DOCUMENTS_A5C_SQL)
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            (_OLD_HEAD_REVISION,),
        )
        conn.commit()
    finally:
        conn.close()


def _run_alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(_BASE_ENV)
    env["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    return subprocess.run(
        [PYTHON, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a5d_migration_upgrade_and_downgrade(tmp_path: Path) -> None:
    db_path = tmp_path / "a5d_test.db"
    _seed_pre_a5d_sqlite_db(db_path)

    # Run upgrade
    res = _run_alembic(db_path, "upgrade", _NEW_HEAD_REVISION)
    assert res.returncode == 0, f"Alembic upgrade failed: {res.stderr}\n{res.stdout}"

    conn = sqlite3.connect(str(db_path))
    try:
        # Check columns added
        cursor = conn.execute("PRAGMA table_info(documents)")
        cols = {row[1] for row in cursor.fetchall()}
        assert "promotion_started_at" in cols
        assert "promotion_completed_at" in cols
        assert "promotion_attempt_count" in cols
        assert "clean_storage_identifier" in cols
        assert "cleanup_pending" in cols

        # Check PROMOTING state accepted under new CHECK constraint
        conn.execute(
            "INSERT INTO documents "
            "(id, project_id, name, file_path, file_type, "
            "created_by_id, created_at, updated_at, ingestion_status) "
            "VALUES ('doc1', 'p1', 'doc.pdf', 'f.upload', "
            "'application/pdf', 'u1', '2026-01-01', '2026-01-01', 'PROMOTING')"
        )
        conn.commit()

        # Check PROMOTION_FAILED state accepted
        conn.execute(
            "INSERT INTO documents "
            "(id, project_id, name, file_path, file_type, "
            "created_by_id, created_at, updated_at, ingestion_status) "
            "VALUES ('doc2', 'p1', 'doc2.pdf', 'f2.upload', "
            "'application/pdf', 'u1', '2026-01-01', '2026-01-01', 'PROMOTION_FAILED')"
        )
        conn.commit()

        # Check invalid status rejected
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO documents "
                "(id, project_id, name, file_path, file_type, "
                "created_by_id, created_at, updated_at, ingestion_status) "
                "VALUES ('doc3', 'p1', 'doc3.pdf', 'f3.upload', "
                "'application/pdf', 'u1', '2026-01-01', '2026-01-01', 'INVALID_STATUS')"
            )

    finally:
        conn.close()

    # Clean up test rows before downgrade so old CHECK constraint passes
    conn_cleanup = sqlite3.connect(str(db_path))
    try:
        conn_cleanup.execute(
            "DELETE FROM documents WHERE ingestion_status IN "
            "('PROMOTING', 'PROMOTION_FAILED')"
        )
        conn_cleanup.commit()
    finally:
        conn_cleanup.close()

    # Run downgrade
    res_down = _run_alembic(db_path, "downgrade", _OLD_HEAD_REVISION)
    assert res_down.returncode == 0, (
        f"Alembic downgrade failed: {res_down.stderr}\n{res_down.stdout}"
    )
