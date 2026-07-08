# RFP Architect - Pilot Risk Register

This risk register tracks all identified security, operational, AI quality, and business risks for the RFP Architect pilot launch.

---

| Risk ID | Category | Description | Severity | Likelihood | Owner | Mitigation | Validation Evidence | Residual Risk | Decision |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **R-01** | AI Quality | LLM Hallucinations: AI generates fictional compliance claims. | High | Medium | AI Lead | Ground drafts strictly on retrieved vector sources. | `test_grounding_rejects_fakes` passes. | Low | **ACCEPT** |
| **R-02** | AI Quality | Missed Requirements: Requirement extractor fails to parse clauses. | High | Low | AI Lead | Trigger on shall/should/must. Match Jaccard similarity. | Evals show recall = 1.000. | Low | **ACCEPT** |
| **R-03** | AI Quality | Unsupported Draft Claims: Draft is generated without source references. | Medium | Low | AI Lead | Set status to `NEEDS_EVIDENCE` and block drafting. | `test_unsupported_drafts` passes. | Low | **ACCEPT** |
| **R-04** | AI Quality | Evidence/Citation Mismatch: AI references incorrect page numbers. | Medium | Low | Dev Team | Parse `[PAGE N]` tags explicitly from document pages. | `test_citation_page_accuracy` passes. | Low | **ACCEPT** |
| **R-05** | Security | Customer Confidential Data Exposure: Ingested documents leak to other users. | Critical | Low | Security | Multi-tenancy isolation filters all queries by `org_id`. | `test_feedback_scoping_and_validation` passes. | Low | **ACCEPT** |
| **R-06** | Security | Auth/Session Misconfiguration: Weak session keys or unauthenticated paths. | High | Low | Security | Signed cookies; redirect unauthenticated users to `/login`. | `test_feedback_requires_auth` passes. | Low | **ACCEPT** |
| **R-07** | Security | Tenant Isolation Defect: Query filters fail to append org scopes. | Critical | Low | Security | Enforce helper functions with manual security audit. | Object-level authorization tests. | Low | **ACCEPT** |
| **R-08** | Operations | Queue/Worker Failure: Parser jobs get stuck in the `arq` queue. | Medium | Medium | DevOps | Configure job retries and health liveness logs. | `test_job_reliability` passes. | Low | **ACCEPT** |
| **R-09** | Operations | Redis/Job Loss: Redis container crash deletes queue records. | Medium | Low | DevOps | Enforce Redis append-only file persistence. | Docker compose AOF config. | Low | **ACCEPT** |
| **R-10** | Operations | Backup Failure: Daily pg_dump fails to save database state. | High | Low | DevOps | Run validation script restoring back up to test target. | `restore.py` script drill. | Low | **ACCEPT** |
| **R-11** | Operations | Migration Failure: Database schema migration breaks current staging DB. | High | Low | Dev Team | Write clean Alembic downgrade schemas. | Staging rehearsal checks. | Low | **ACCEPT** |
| **R-12** | Operations | Docker Deployment Misconfiguration: Incorrect container variables in production. | High | Low | DevOps | Validate compose files using config tools. | `docker compose config` passes. | Low | **ACCEPT** |
| **R-13** | Security | CI Scan False Negatives: Security vulnerabilities bypass gate checks. | High | Medium | Security | Integrate Trivy vulnerability scans in release gates. | `release.yml` gate checks. | Low | **ACCEPT** |
| **R-14** | Business | Unsupported Compliance Claims: Sales materials claim SOC 2 certified. | Critical | Medium | Sales Ops | Outreach guardrails prohibit cert claims in emails. | `test_sales_ops_artifacts_exist` passes. | Low | **ACCEPT** |
| **R-15** | Business | Low Willingness-to-Pay: Target accounts refuse to convert. | High | Medium | Product | Track CSAT, SME time saved, and exit survey metrics. | `PILOT_SUCCESS_METRICS.md` KPIs. | Medium | **ACCEPT** |
