# RFP Architect Option A - Reviewer Guide

This guide assists engineering, security, and product reviewers in auditing the Option A release candidate on `main` (`4cdee3ba27d853f4158b78f9a57ec4bfdc5b6d21`).


---

## 1. Recommended Review Order

To review the changes efficiently, follow this logical order:
1. **Core Security controls:** Session authentication, CSRF validation, and tenant isolation filters.
2. **Database & Migrations:** DB model extensions and Alembic schema migrations.
3. **AI Quality & Grounding:** Requirement extraction prompts, Jaccard overlap evals, and page number tracking.
4. **Operations & Observability:** Backup recovery scripts, structured logs, and Docker container settings.
5. **Commercial & Onboarding Guides:** Qualification scorecards, safety guardrails, and outreach checklists.

---

## 2. Review Focus Areas & Key Files

### Security Review Focus:
* *Goal:* Verify zero cross-tenant leakage and session integrity.
* *Files to inspect first:*
  * [app/core/security.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/security.py) (auth session and token parsing checks)
  * [app/core/csrf.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/csrf.py) (CSRF token verification rules)
  * [app/web/routes/feedback.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/feedback.py) (scoping and organization ownership validation)

### AI/Eval Review Focus:
* *Goal:* Verify prompt injection controls and Jaccard-overlap evaluations.
* *Files to inspect first:*
  * [app/core/llm.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/llm.py) (Jaccard deduplication and system prompt guardrails)
  * [scripts/run_ai_eval.py](file:///D:/RFA/Project/rfp-architect-mvp/scripts/run_ai_eval.py) (Golden case eval matching logic)

### Database, Migration & Deployment Review Focus:
* *Goal:* Verify safe migrations and container health probes.
* *Files to inspect first:*
  * [alembic/versions/7a14e99f1390_create_pilot_feedback_table.py](file:///D:/RFA/Project/rfp-architect-mvp/alembic/versions/7a14e99f1390_create_pilot_feedback_table.py) (SQL DDL schema changes)
  * [Dockerfile](file:///D:/RFA/Project/rfp-architect-mvp/Dockerfile) (Multi-stage build settings)
  * [docker-compose.prod.yml](file:///D:/RFA/Project/rfp-architect-mvp/docker-compose.prod.yml) (Services environment setup)

### Commercial Safety & Guardrails Review Focus:
* *Goal:* Verify outreach templates contain no active compliance claims.
* *Files to inspect first:*
  * [AI_CLAIMS_AND_OUTREACH_GUARDRAILS.md](file:///D:/RFA/Project/rfp-architect-mvp/AI_CLAIMS_AND_OUTREACH_GUARDRAILS.md) (Prohibited claims section)
  * [tests/integration/test_step15_commercial.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_step15_commercial.py) (Compliance checks)

---

## 3. Local Audit Commands to Run

Verify the branch health locally by running:
```powershell
# Run the complete release validation script
powershell -ExecutionPolicy Bypass -File scripts/final_release_validation.ps1 -SkipDockerBuild

# Or execute specific python integration tests
.\.venv\Scripts\pytest.exe -vv tests/integration/
```

---

## 4. Merge Blockers (What Should Block Merge)

Reviewers must **BLOCK** the pull request merge if:
* A mutating endpoint (POST/PUT/PATCH/DELETE) is missing CSRF token verification.
* Any database model query fails to scope results by the authenticated user's `organization_id`.
* Sales/outreach files make active, audited SOC 2, GDPR, or HIPAA certification claims.
* The test suite has failing checks or the linter has warnings.
