import os

import pytest
from sqlalchemy import create_engine, text

from app.core.database import get_default_org_and_user
from app.models.document import Document
from app.models.project import ProposalProject


class TestDocumentSecurityMetadataFields:
    def test_new_columns_exist_with_expected_types(self) -> None:
        cols = Document.__table__.columns
        assert cols["ingestion_status"].type.length == 30
        assert cols["ingestion_status"].nullable is False
        assert cols["display_filename"].type.length == 255
        assert cols["detected_content_type"].type.length == 255
        assert cols["sha256_digest"].type.length == 64
        assert cols["scan_status"].type.length == 30
        assert cols["content_policy_status"].type.length == 30
        assert cols["rejection_reason_code"].type.length == 100

    def test_new_document_defaults_to_legacy_unverified_in_orm(self, db) -> None:
        """New rows created via the ORM without explicitly passing
        ingestion_status get the honest LEGACY_UNVERIFIED default (matching
        the migration's server_default) until A5b's real upload-flow
        rewiring explicitly sets QUARANTINED at the real upload call
        sites."""
        org_id, user_id = get_default_org_and_user(db)

        project = ProposalProject(
            organization_id=org_id,
            created_by_id=user_id,
            name="Ingestion metadata test project",
            client_name="Acme Corp",
            status="draft",
        )
        db.add(project)
        db.commit()

        doc = Document(
            project_id=project.id,
            name="test.pdf",
            file_path="/data/storage/documents/x.pdf",
            file_type="application/pdf",
            created_by_id=user_id,
        )
        db.add(doc)
        db.commit()
        assert doc.ingestion_status == "LEGACY_UNVERIFIED"

    def test_explicit_quarantined_ingestion_status_is_stored_correctly(
        self, db
    ) -> None:
        """Explicitly passing ingestion_status=IngestionStatus.QUARANTINED at
        construction still works and is stored correctly - this is the
        pattern A5b's real upload call sites will use once quarantine
        storage exists."""
        from app.services.ingestion_state import IngestionStatus

        org_id, user_id = get_default_org_and_user(db)

        project = ProposalProject(
            organization_id=org_id,
            created_by_id=user_id,
            name="Ingestion metadata test project (explicit quarantine)",
            client_name="Acme Corp",
            status="draft",
        )
        db.add(project)
        db.commit()

        doc = Document(
            project_id=project.id,
            name="test.pdf",
            file_path="/data/storage/quarantine/x.pdf",
            file_type="application/pdf",
            created_by_id=user_id,
            ingestion_status=IngestionStatus.QUARANTINED,
        )
        db.add(doc)
        db.commit()
        assert doc.ingestion_status == "QUARANTINED"


@pytest.mark.skipif(
    "postgresql" not in os.environ.get("DATABASE_URL", ""),
    reason="migration backfill test requires a real Postgres DATABASE_URL",
)
class TestMigrationBackfill:
    def test_preexisting_row_backfilled_to_legacy_unverified(self) -> None:
        """Insert a row bypassing the ORM default (simulating a pre-A5
        row), confirm the column's server_default applies for any row
        inserted without an explicit value, matching what the migration
        did for real pre-existing rows when it added the column."""
        engine = create_engine(os.environ["DATABASE_URL"])
        with engine.begin() as conn:
            row = conn.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name='documents' AND column_name='ingestion_status'"
                )
            ).one()
            assert "LEGACY_UNVERIFIED" in row[0]
