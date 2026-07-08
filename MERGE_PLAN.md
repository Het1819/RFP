# RFP Hardening Branch - Merge Plan

This document maps out the strategy, approval requirements, and rollback procedures to safely merge the `hardening-pilot-readiness` branch into the main line.

---

> [!IMPORTANT]
> **DO NOT MERGE OR PUSH AUTOMATICALLY:** All merges must be approved by the designated code owner, verified by the CI pipeline gates, and executed manually.

---

## 1. Branch Merge Strategy

* **Target Base Branch:** `main` (or designated production-ready integration branch).
* **Merge Method:** **Rebase and Merge** (to preserve a clean, linear git commit history) or **Squash and Merge** (squashing individual step commits into clear functional blocks).
* **CI Failure Policy:** If the CI build fails at any gate check (lint, format, typecheck, or test), the merge is blocked. No manual bypasses are permitted.

---

## 2. Collision & Conflict Resolution

### Database Migration Conflicts:
* If new migrations have been committed to `main` during our hardening sprint, we must:
  1. Rebase our branch onto the latest `main`.
  2. Use Alembic history tools to verify migration order.
  3. Rename or adjust the head revision pointers in our Step 14 migration file to point to the new `main` migration head.

### Environment Variable Updates:
* Staging/production environment hosts must be configured with new parameters before the merge is completed. Ensure `SESSION_SECRET_KEY` is set to a secure, random 32-character string.

---

## 3. Manual Approval Gates

Merging requires at least **two approved reviews** from the following roles:
1. **Security Owner:** Verifies tenant isolation and session authorization checks.
2. **Lead Developer:** Verifies migration safety, model schemas, and tests.

---

## 4. Post-Merge Tagging Strategy

Immediately following a successful merge into `main`:
1. Delete the local/remote `hardening-pilot-readiness` branch (after checking that commits are saved on `main`).
2. Apply an annotated release candidate tag to `main`:
   ```bash
   git tag -a v1.0.0-rc1 -m "Release Candidate 1 - Hardened baseline through Step 17"
   ```
3. Deploy to the staging environment and execute the smoke-testing runbook.
