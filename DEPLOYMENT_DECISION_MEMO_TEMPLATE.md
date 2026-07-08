# Deployment Decision Memo Template

**Release Candidate Tag:** [e.g., v1.0.0-rc1]  
**Evaluation Date:** [Date]  
**Approvers:**  
* [Name] - Engineering Lead
* [Name] - Security Lead
* [Name] - Product Owner

---

## 1. Scope of Release

Provide a summary of the database migrations, core API endpoints, and onboarding materials included in this release candidate.

---

## 2. Validation & Quality Checklist

* **CI Pipeline Status:** [PASS / FAIL]
* **Pytest Success Rate:** [100% / list failures]
* **Offline AI Eval Recall:** [Actual value, e.g., 1.000]
* **Staging Smoke Test Status:** [PASS / FAIL]
* **Vulnerability trivy Scan:** [0 Critical / list exceptions]

---

## 3. Risks & Approvals

### Risks Accepted:
* *Risk:* AI may occasionally flag valid requirements as `NEEDS_REVIEW` due to low confidence. *Reason for acceptance:* Prefer false positives requiring human review over missing compliance items.
* *Risk:* Lack of SOC 2/HIPAA audit certifications. *Reason for acceptance:* Acceptable for staging staging trials under enterprise zero-retention API agreements.

### Risks NOT Accepted (Release Blockers):
* Any unauthenticated mutating POST endpoint.
* Unhandled migration script conflicts.
* Failure of DB connection check `/readyz`.

---

## 4. Operational Support & Monitoring Plan

* **Deployment target:** Dedicated staging ECS/VPC host.
* **Monitoring:** Review structured JSON log outputs via CloudWatch/Datadog. Check CPU and Redis memory usage.
* **Support SLA:** Blocker bugs resolved within 4 business hours.
* **Rollback Plan:** If the deployment fails, trigger ECS container rollback to the previous stable release candidate tag, and execute Alembic DB downgrade migrations.

---

## 5. Final Release Decision

Choose one:
* [ ] **OPTION A: Approve Staging Deployment.** Provision staging infrastructure and run seed scripts.
* [ ] **OPTION B: Approve Controlled Customer Pilot.** Launch the pilot onboarding workflow for the first batch of accounts.
* [ ] **OPTION C: Block Release.** Return candidate to the dev team with specific fix targets.

**Signatures:**  
Engineering: ___________________ Product: ___________________ Security: ___________________
