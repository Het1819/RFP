# RFP Architect - Final Release Checklist (Option A)

This operational checklist must be fully executed and signed off prior to packaging or releasing Option A candidates.

> **Canonical Release Specification:**
> Refer to [docs/release/A6_CONTROLLED_RELEASE_GATE.md](file:///D:/RFA/Project/rfp-architect-mvp/docs/release/A6_CONTROLLED_RELEASE_GATE.md) for full gate criteria, required evidence, approval states, and stop conditions.

---

## 1. Branch, SHA, & CI Verification

- [ ] **Immutable Source SHA:** Confirm release target is `main` at commit `4cdee3ba27d853f4158b78f9a57ec4bfdc5b6d21`.
- [ ] **Working Tree & PR Status:** Confirm clean working tree (`git status --porcelain=v1` clean) and 0 open pull requests.
- [ ] **CI Status:** Confirm all 7 GitHub Actions checks passed at target SHA (`ai-evals`, `backend-quality`, `docker-build`, `edge-security`, `frontend-quality`, `release-gate`, `security-scan`).
- [ ] **Secret Audit:** Ensure zero production passwords, API keys, or raw secrets exist in repository code or logs.
- [ ] **Container & Dependency Scanning:** `pip-audit`, `npm audit`, Gitleaks, and Trivy report 0 high/critical vulnerabilities.

---

## 2. Core Security & Isolation Controls

- [ ] **Authentication & Sessions:** Enforce password auth (Argon2), server-side Redis opaque sessions, and login throttling.
- [ ] **CSRF Verification:** Mutating actions require valid CSRF tokens verified by middleware.
- [ ] **Tenant Isolation:** Enforce organization-level tenant isolation across all project resources.
- [ ] **Upload State Machine:** Document upload transitions strictly through `QUARANTINED` → `VALIDATING` → `SCANNING` → `CLEAN_PENDING_PROMOTION` → `PROMOTING` → `CLEAN` → `PARSING` → `COMPLETED`.
- [ ] **Malware & Isolation:** ClamAV scanning active; parser isolated on dedicated `parser_net` bridge network without DB/storage credentials.

---

## 3. Product Workflows & AI Grounding

- [ ] **Evidence & Provenance:** Requirement candidates retain exact page numbers and evidence provenance.
- [ ] **AI Eval Validation:** Offline AI eval suite passes accuracy and grounding thresholds.
- [ ] **Governed Human Review:** Candidate review tasks require authorized human reviewer approval (`user.can_review_requirements`). Ordinary users cannot approve.
- [ ] **Provider Default:** LLM requirement extraction provider is disabled by default (`LLM_PROVIDER=disabled`). Zero live provider calls.

---

## 4. Observability & Operational Readiness

- [ ] **Database Migrations & Rollback:** Alembic migrations applied to head (`a4b5c6d7e8f9`); rollback downgrades verified.
- [ ] **7-Service Docker Topology:** Stack verified (`nginx`, `app`, `worker` queue, `postgres`, `redis`, `clamd`, `parser`).
- [ ] **Backup Recovery:** Database backup scripts and recovery workflows verified.
- [ ] **Session Revocation:** Emergency session revocation script verified (`scripts/revoke_user_sessions.py`).

---

## 5. Formal Approval Sign-off

- **Engineering Lead Sign-off:** _________________ Date: _______________
- **Security Officer Sign-off:** _________________ Date: _______________
- **Product Manager Sign-off:** _________________ Date: _______________
- **Release Decision:** **[ BLOCKED / READY FOR RELEASE-CANDIDATE REVIEW / APPROVED FOR PACKAGING ]**
