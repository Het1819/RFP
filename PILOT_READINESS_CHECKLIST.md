# RFP Architect MVP - Pilot Readiness Checklist

This checklist defines the operational, security, and quality gates required before initiating the controlled pilot launch of the RFP Architect MVP.

---

## 1. Code & Release Status
- [ ] **Branch Merged:** Branch `hardening-pilot-readiness` is fully tested, reviewed, and merged into `main`/`master`.
- [ ] **Tagging:** Current release commit is tagged with `pilot-hardening-step*` (specifically `pilot-hardening-step12` or later).
- [ ] **No Direct Commits:** Branch protection rules configured on GitHub to disable direct commits.

## 2. CI/CD Gates
- [ ] **All Checks Passing:** GitHub Actions `CI Quality Gates` pipeline completes successfully on the release branch.
- [ ] **Release Job Completed:** `Release Workflow` verification completes with no errors and uploads the build artifact bundle.
- [ ] **No Hardcoded Secrets:** Static analysis verifies that no real API keys (`sk-`, etc.) or passwords exist in workflow files.

## 3. Environment & Secrets
- [ ] **Staging Configuration:** `.env` is initialized on staging from `.env.staging.example`.
- [ ] **Secret Strength:** All secret keys (`SESSION_SECRET_KEY`, `APP_SECRET_KEY`) are generated using cryptographically secure random values (minimum 32 characters).
- [ ] **Secure Storage:** Staging database credentials and external API keys are injected via secure environment variables or vault systems, not hardcoded.
- [ ] **Provider Selection:** `LLM_PROVIDER` is set to `anthropic` or `openai` for live pilot, or `fake` for testing.

## 4. Database Migrations
- [ ] **Alembic Status:** All database migrations are run up to date (`alembic upgrade head`) on the staging database.
- [ ] **Schema Validation:** Verification that the schema matches the production/staging target state and `pgvector` extension is active.
- [ ] **Pruning and Seeding:** Staging DB is pruned of development junk and populated only with verified synthetic demo data using `scripts/seed_pilot_demo.py`.

## 5. Redis & Queue Workers
- [ ] **Redis Running:** Staging Redis instance is healthy and accepting connections.
- [ ] **Workers Active:** Worker service (`arq app.worker.WorkerSettings`) is active, running under unprivileged user, and successfully processing background jobs.
- [ ] **Task Retries:** Redis queue has retry logic enabled for document extraction tasks.

## 6. Backup & Recovery Drill
- [ ] **Backup Executed:** Manual database backup verified via `pg_dump` to `BACKUP_DIR`.
- [ ] **Restore Validated:** Successful database restore drill performed to a separate staging/test database instance.
- [ ] **Asset Preservation:** Uploaded files (`UPLOAD_DIR`) are backed up and restorable.
- [ ] **Redis State Persistence:** Redis persistent append-only files (AOF) are configured.

## 7. Health & Readiness
- [ ] **Liveness Probe:** `/healthz` endpoint returns `200 OK` (liveness checks).
- [ ] **Readiness Probe:** `/readyz` endpoint returns `200 OK` (database connection verification).
- [ ] **Smoke Tests:** Local smoke test scripts (`scripts/smoke_test.ps1` or `scripts/smoke_test.sh`) pass cleanly against the target environment.

## 8. Logging, Observability & Metrics
- [ ] **Correlation IDs:** Verify all backend request logs contain correlation IDs propagating across middlewares and worker tasks.
- [ ] **Prometheus Registry:** `/metrics` endpoint is operational and exposes telemetry data.
- [ ] **Log Level:** `LOG_LEVEL` is set to `INFO` (suppressing verbose `DEBUG` output but retaining security events).
- [ ] **No PII Leaks:** Observability logging does not print sensitive draft content or customer document contents.

## 9. Security Controls
- [ ] **CSRF Defense:** All mutating web forms include double-submit CSRF tokens.
- [ ] **Session Authentication:** Cookied sessions are signed, use `HttpOnly`, `Secure`, and `SameSite=Lax` parameters.
- [ ] **Object-Level Auth:** Access controls verify that projects and requirements can only be accessed by users belonging to the project's owning organization.
- [ ] **Dependencies Audited:** Python (`pip-audit`) and Node (`npm audit`) vulnerability checks show zero critical vulnerabilities.
- [ ] **Trivy / Container Scan:** Docker filesystem scan shows zero critical or high-severity vulnerabilities.

## 10. AI Evaluation & Accuracy Gates
- [ ] **Offline Evals Pass:** Run `python scripts/run_ai_eval.py --offline` and verify all pilot metrics (Recall >= 0.90, F1 = 1.000, 0 Hallucinations) pass.
- [ ] **Grounding Verification:** The grounding engine successfully rejects unsupported claims and flags missing evidence as `NEEDS_EVIDENCE`.
- [ ] **Source Page Preservation:** Document processing extracts and correlates metadata including original page numbers and sources.

## 11. Human Review & Gating
- [ ] **Human-in-the-Loop:** All draft answers require a human reviewer to mark them as `APPROVED` before export.
- [ ] **Gap Routing:** Gaps are successfully routed to human reviewers under the `NEEDS_REVIEW` state.

## 12. Export Approval Gates
- [ ] **Authorized Exports:** DOCX and XLSX exports require project-level read/write permissions.
- [ ] **Compliance Matrix:** Exported XLSX matches the compliance matrix layout.

## 13. Accessibility & Compatibility Quick Checks
- [ ] **Keyboard Navigable:** Ensure interactive items in the workspace UI are keyboard navigable.
- [ ] **Contrast Check:** Verify color contrast meets WCAG 2.1 AA requirements (especially in dark mode).
- [ ] **Browser Testing:** Verified interface functionality in modern versions of:
  - Chrome / Chromium
  - Firefox
  - Safari
  - Microsoft Edge

## 14. Incident Response & Support Operations
- **Incident Commander:** Primary contact: Het Patel (`het.patel@yourorg.com`).
- **Support Escalation Plan:**
  1. Infrastructure failure -> Re-deploy container images or rollback to previous tag.
  2. Data corruption -> Restore database from the latest pg_dump snapshot.
  3. LLM failure / API downtime -> Revert `LLM_PROVIDER` fallback or monitor Anthropic/OpenAI status.

---

## 15. Rollback Procedure Quick Reference
If the staging or pilot deployment fails smoke tests or experiences database/worker crash:
1. Revert target code branch to `pilot-hardening-step12`.
2. Run migrations rollback if schema modification failed (`alembic downgrade`).
3. Rebuild and restart docker images using the `pilot` tag.
4. Run `scripts/smoke_test.ps1` to confirm restoration.

---

## 16. Customer & Pilot Onboarding Checklist
- [ ] **Org Setup:** Create dedicated pilot Organization database record.
- [ ] **User Provisioning:** Create initial user credentials.
- [ ] **Quota Settings:** Restrict maximum uploads (e.g., 5 files per project, max 20MB per file).

---

## 17. Go/No-Go Decision Criteria

The following matrix must be signed off by the team before launching the pilot:

| Gate Category | Metric / Condition | Go Threshold | Actual Status | Pass/Fail |
| :--- | :--- | :--- | :--- | :--- |
| **Testing** | Pytest Suite Pass Rate | 100% | 100% | PASS |
| **Security** | Dependency Scanning | 0 Critical Vulns | 0 Critical Vulns | PASS |
| **AI Quality** | Recall (Golden cases) | >= 0.90 | 1.000 | PASS |
| **AI Grounding**| Evidence Grounding Pass | No Fabricated Evidence | Rejects fakes | PASS |
| **Operations** | Readiness Probe | `/readyz` returns 200 | Returns 200 | PASS |
| **Infrastructure**| Smoke Test script | All steps pass | All steps pass | PASS |
| **Recovery** | Backup/Restore Drill | Verified successful restore | Drill verified | PASS |
| **Feedback** | Feedback Capture | In-app route operational | POST /feedback | PASS |
| **Material** | Pilot Execution Docs | Onboarding, Quickstart, Data Handling exist | Verified | PASS |
| **Triage** | Issue Escalation | Triage workflow and exit templates defined | Verified | PASS |
| **Commercial** | Paid Conversion Docs | Qualification, pricing, ROI, scripts defined | Verified | PASS |
| **Sales Ops** | Outreach System | Templates, sequences, scorecards, pipeline defined | Verified | PASS |
| **Campaign** | Launch Campaign | Account tracker, QA checks, learning logs defined | Verified | PASS |
| **Release** | PR & Release Package | PR template, risk register, reviewer guide defined | Verified | PASS |

---

## 18. Step 15 - Commercial Readiness Checklist

- [ ] **ICP & Buyer Scorecard:** Verify `ICP_QUALIFICATION_SCORECARD.md` is shared with the sales/ops team.
- [ ] **Paid Pilot Offer:** Confirm `PAID_PILOT_OFFER.md` scopes (2/4/6 weeks) and exclusions are aligned.
- [ ] **Pricing and Packaging:** Ensure target price points ($2.5k, $5k, $10k) and SaaS margin metrics are approved.
- [ ] **Discovery and Demo Scripts:** Practice scripts to avoid overpromising compliance features.
- [ ] **Security Response Pack:** Maintain questionnaire answers with `[IMPLEMENTED]` / `[DOCUMENTED]` labels and no fake claims.
- [ ] **ROI Calculator Guide:** Validate ROI estimations and conservative/aggressive payback scenarios.
- [ ] **Objection Handling Guide:** Review honest technical rebuttals regarding hallucinations and security boundaries.
- [ ] **Conversion Criteria:** Track required technical and business triggers before transitioning to paid rollout.

---

## 19. Step 16 - Outreach Execution and Sales Pipeline Operations Checklist

- [ ] **Target Account List:** Confirm `TARGET_ACCOUNT_LIST_TEMPLATE.csv` columns and sample rows are validated.
- [ ] **CRM Pipeline Template:** Confirm `CRM_PIPELINE_TEMPLATE.csv` stages (including WON/LOST/DISQUALIFIED) are mapped.
- [ ] **Outreach Templates:** Confirm email and LinkedIn message copy does not make false win/ROI/compliance claims.
- [ ] **Sales Sequence Cadence:** Follow the 10-business-day schedule and honor opt-out requests within the 1-day SLA.
- [ ] **Discovery & Demo Scorecards:** Keep qualification scorecards ready for all new calls.
- [ ] **Claim Safety Guardrails:** Review the prohibited claims section before executing outbound pitches.

---

## 20. Step 17 - First Paid Pilot Campaign Execution Checklist

- [ ] **First 20-Account Batch:** Verify `FIRST_20_ACCOUNT_BATCH_TEMPLATE.csv` placeholder rows and columns match outreach specifications.
- [ ] **Account Research Worksheets:** Use only approved public sources ( SAM.gov, company website); do not use automated scrapers.
- [ ] **Outbound QA Verification:** Run QA checklists on every personalized message prior to sending.
- [ ] **Discovery Evidence Logging:** Document specific customer quote placeholders and WTP evidence in the template.
- [ ] **Opportunity Memo Review:** Verify opportunity review memos are filled out for GO deals.
- [ ] **Weekly Campaign Reports:** Maintain weekly performance metrics, segment learning logs, and action items.
- [ ] **Win/Loss Log:** Audit outcomes and pricing/security reactions in `WIN_LOSS_LEARNING_LOG.csv`.
- [ ] **Customer Proof Integrity:** Confirm the proof repository enforces written permission requirements and forbids fake reviews.

---

## 21. Step 18 - PR and Release Review Readiness Checklist

- [ ] **PR Description:** Verify `PR_DESCRIPTION_HARDENING_BRANCH.md` describes security, AI, and container updates.
- [ ] **Final Release Checklist:** Execute `FINAL_RELEASE_CHECKLIST.md` items before merging.
- [ ] **Risk Register Audit:** Review all 15 operational/security/business risks logged in `PILOT_RISK_REGISTER.md`.
- [ ] **Reviewer Guide Walkthrough:** Audit key directories and run local test validation commands.
- [ ] **Merge & Downgrade Plan:** Check Alembic downgrade paths and rebase merge guidelines.
- [ ] **Deployment Decision Memo:** Complete the approver template prior to pilot environment staging.
- [ ] **Final Validation Script:** Confirm `final_release_validation.ps1` runs clean on all gates.





