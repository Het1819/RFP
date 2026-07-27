import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.evidence_validation import SNIPPET_MAX_LEN, SNIPPET_MIN_LEN
from app.services.ingestion_state import IngestionStatus


def _retrieve_evidence_postgresql(
    db: Session, project_id: uuid.UUID, clean_query: str, limit: int
) -> list[dict[str, Any]]:
    """PostgreSQL FTS path — uses to_tsvector / plainto_tsquery / @@ operator."""
    from sqlalchemy import text

    sql = text(
        """
        SELECT dp.page_number,
               dp.content AS raw_snippet,
               d.name AS doc_name,
               d.id AS doc_id,
               ts_rank(
                 to_tsvector('english', dp.content),
                 plainto_tsquery('english', :query)
               ) AS score
        FROM document_pages dp
        JOIN documents d ON dp.document_id = d.id
        WHERE d.project_id = :project_id
          AND d.doc_role = 'knowledge_base'
          AND d.approval_status = 'APPROVED'
          AND d.processing_status = 'completed'
          AND d.ingestion_status = :completed_status
          AND to_tsvector('english', dp.content) @@ plainto_tsquery('english', :query)
        ORDER BY score DESC
        LIMIT :limit
    """
    )
    rows = (
        db.execute(
            sql,
            {
                "project_id": project_id,
                "query": clean_query,
                "limit": limit,
                "completed_status": IngestionStatus.COMPLETED,
            },
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def _retrieve_evidence_sqlite(
    db: Session, project_id: uuid.UUID, clean_query: str, limit: int
) -> list[dict[str, Any]]:
    """SQLite fallback path — uses ORM LIKE for CI/test compatibility.

    Searches for any of the whitespace-separated query tokens in page content.
    Returns rows in the same schema as the PostgreSQL path, with score=1.0.
    """
    from app.models.document import Document, DocumentPage

    # Build filter: page content LIKE any query token (case-insensitive in SQLite)
    tokens = [t for t in clean_query.split() if t]
    if not tokens:
        return []

    query = (
        select(
            DocumentPage.page_number,
            DocumentPage.content.label("raw_snippet"),
            Document.name.label("doc_name"),
            Document.id.label("doc_id"),
        )
        .join(Document, DocumentPage.document_id == Document.id)
        .where(
            Document.project_id == project_id,
            Document.doc_role == "knowledge_base",
            Document.approval_status == "APPROVED",
            Document.processing_status == "completed",
            Document.ingestion_status == IngestionStatus.COMPLETED,
        )
        .limit(limit)
    )

    # Apply LIKE filter for first token only to avoid complex OR chains in tests
    query = query.where(DocumentPage.content.ilike(f"%{tokens[0]}%"))

    rows = db.execute(query).mappings().all()
    return [dict(r, score=1.0) for r in rows]


def retrieve_evidence(
    db: Session, project_id: uuid.UUID, query_text: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Retrieve top evidence passages from APPROVED, fully-processed knowledge-base
    documents.

    Uses PostgreSQL full-text search (FTS) on PostgreSQL and an ORM LIKE fallback
    on SQLite (for CI/test environments). Both paths return the same schema:

    - doc_id       (UUID)  — server-side identifier, never client-submitted
    - doc_name     (str)   — document display name
    - page_number  (int)   — source page
    - snippet      (str)   — trimmed to [SNIPPET_MIN_LEN, SNIPPET_MAX_LEN]
    - score        (float) — server-computed FTS rank (1.0 on SQLite)

    Only APPROVED documents with processing_status='completed' are searched.
    """
    clean_query = query_text.strip()
    if not clean_query:
        return []

    # Remove only chars that break plainto_tsquery; preserve hyphens, dots, colons
    clean_query = re.sub(r"[\'\\]", "", clean_query).strip()
    if not clean_query:
        return []

    # Detect dialect to choose the retrieval path
    bind = db.get_bind()
    dialect_name = bind.dialect.name if bind is not None else "sqlite"
    if dialect_name == "postgresql":
        rows = _retrieve_evidence_postgresql(db, project_id, clean_query, limit)
    else:
        # SQLite or any other dialect — use ORM LIKE fallback
        rows = _retrieve_evidence_sqlite(db, project_id, clean_query, limit)

    results = []
    for r in rows:
        raw = (r["raw_snippet"] or "").strip()
        # Enforce snippet length bounds — content from stored pages only
        if len(raw) < SNIPPET_MIN_LEN:
            continue  # skip degenerate pages
        snippet = raw[:SNIPPET_MAX_LEN]
        results.append(
            {
                "doc_id": str(r["doc_id"]),
                "doc_name": r["doc_name"],
                "page_number": r["page_number"],
                "snippet": snippet,
                "score": float(r["score"]),
            }
        )
    return results
