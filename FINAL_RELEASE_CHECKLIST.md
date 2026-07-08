# RFP Architect - Final Release Checklist

This checklist must be fully executed and signed off prior to merging the `hardening-pilot-readiness` branch into the main line and releasing to staging.

---

## 1. Branch, CI, & Secret Reviews

- [ ] **Branch Verification:** Confirm active branch is `hardening-pilot-readiness`.
- [ ] **Annotated Tags:** Verify tags `pilot-hardening-step12` through `pilot-hardening-step17` exist.
- [ ] **CI Pipeline Status:** Confirm all Ruff, Mypy, and Pytest checks pass on the build server.
- [ ] **Secret Audit:** Ensure no production passwords, API keys, or raw JWT secrets exist in code or logs.
- [ ] **Container Scanning:** Check that Trivy reports 0 Critical vulnerabilities on the built container.

---

## 2. Core Security & Isolation Controls

- [ ] **Authentication & Session:** Enforce explicit cookie session authentication (redirects unauthenticated users to `/login`).
- [ ] **CSRF Verification:** Mutating actions require valid CSRF tokens verified by the middleware.
- [ ] **Tenant Isolation:** Database queries append strict `organization_id` parameters matching session user metadata.
- [ ] **Object Authorization:** Resources (projects, requirements, feedback) verify organization ownership before access.

---

## 3. Product Workflows & AI Grounding

- [ ] **Evidence & Citation Validation:** Draft generation matches verified source page numbers and highlights grounding text.
- **AI Evaluation Metrics:** Golden test evals achieve recall >= 90% and precision validation thresholds.
- [ ] **Export Approval Gating:** Requirements must be approved by a human reviewer before export is unlocked.
- [ ] **Queue / Worker Processing:** Long-running jobs are routed via Redis `arq` with crash recovery support.

---

## 4. Observability & Operations

- [ ] **Backups:** Validate postgres daily pg_dump restore workflows and verify recovery scripts.
- [ ] **Rollback Plan:** Confirm Alembic database schema downgrade files are verified.
- [ ] **Smoke Testing:** Execute cross-platform `smoke_test.ps1`/`smoke_test.sh` against the deployed staging environment.
- [ ] **In-App Feedback Capture:** Ensure POST `/feedback` route saves entries correctly with tenant scoping.

---

## 5. Sales Enablement & Pilot Campaign

- [ ] **Sales Materials Review:** Ensure pricing sheets, discovery scripts, and proposals make no uncertified SOC 2 or HIPAA claims.
- [ ] **Proof Rules:** Testimonial logs prohibit fake quotes and enforce written legal permission rules.
- [ ] **Weekly Cadence:** Schedule Monday pipeline reviews and Friday campaign metrics dashboard checks.

---

## 6. Final Go/No-Go Approval

* **Engineering Lead Sign-off:** _________________ Date: _______________  
* **Security Officer Sign-off:** _________________ Date: _______________  
* **Product Manager Sign-off:** _________________ Date: _______________  
* **Go/No-Go Decision:** **[GO / HOLD]**
