---
name: vertical-slice
description: Implement one small, testable RFP Architect MVP feature from schema through UI.
---

When implementing a feature:

1. Read AGENTS.md and relevant existing code.
2. Restate the narrow acceptance criteria.
3. Add or update database models and Alembic migration only if needed.
4. Add Pydantic schemas.
5. Add service-layer logic.
6. Add API routes and Jinja/HTMX UI.
7. Add unit and integration tests.
8. Run make check.
9. Summarize changed files, test output, limitations, and next step.

Never:
- Add unrelated integrations.
- Use real customer data.
- Call a real LLM from tests.
- Generate an answer without evidence links.
