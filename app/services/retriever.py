import re
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def retrieve_evidence(
    db: Session, project_id: uuid.UUID, query_text: str, limit: int = 5
) -> list[dict[str, Any]]:
    """Retrieve top evidence passages from APPROVED knowledge base documents.

    Uses full-text search (FTS).
    """
    clean_query = query_text.strip()
    if not clean_query:
        return []

    # Remove only chars that break plainto_tsquery;
    # preserve hyphens, dots, parens, colons
    clean_query = re.sub(r"[\'\\]", "", clean_query).strip()
    if not clean_query:
        return []

    sql = text(
        """
        SELECT dp.page_number, dp.content as snippet,
               d.name as doc_name, d.id as doc_id,
               ts_rank(
                 to_tsvector('english', dp.content),
                 plainto_tsquery('english', :query)
               ) as score
        FROM document_pages dp
        JOIN documents d ON dp.document_id = d.id
        WHERE d.project_id = :project_id
          AND d.doc_role = 'knowledge_base'
          AND d.approval_status = 'APPROVED'
          AND to_tsvector('english', dp.content) @@ plainto_tsquery('english', :query)
        ORDER BY score DESC
        LIMIT :limit
    """
    )

    results = (
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

    return [dict(r) for r in results]
