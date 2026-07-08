import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.evidence_validation import SNIPPET_MAX_LEN, SNIPPET_MIN_LEN


def retrieve_evidence(
    db: Session, project_id: uuid.UUID, query_text: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Retrieve top evidence passages from APPROVED, fully-processed knowledge-base
    documents using PostgreSQL full-text search (FTS).

    Each result includes:
    - doc_id       (UUID)  — server-side identifier, never client-submitted
    - doc_name     (str)   — document display name
    - page_number  (int)   — source page
    - snippet      (str)   — trimmed to [SNIPPET_MIN_LEN, SNIPPET_MAX_LEN]
    - score        (float) — server-computed FTS rank

    Only APPROVED documents with processing_status='completed' are searched.
    """
    clean_query = query_text.strip()
    if not clean_query:
        return []

    # Remove only chars that break plainto_tsquery; preserve hyphens, dots, colons
    clean_query = re.sub(r"[\'\\]", "", clean_query).strip()
    if not clean_query:
        return []

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
            },
        )
        .mappings()
        .all()
    )

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
