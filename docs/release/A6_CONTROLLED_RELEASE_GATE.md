# Option A Controlled Release Gate Specification

This document defines the canonical release gate criteria, required evidence, approval states, and operational controls for Option A releases of the RFP Architect workspace.

---

## 1. Immutable Release Source

Every release candidate must originate strictly from `main` at an explicitly recorded, immutable git commit SHA.

- **Target SHA**: `4cdee3ba27d853f4158b78f9a57ec4bfdc5b6d21` (`4cdee3b`)
- **Working Tree**: Clean (`git status --porcelain=v1` must return zero output)
- **Open Pull Requests**: Exactly 0 open PRs
- **Integrated CI Status**: All seven GitHub Actions checks passed at the candidate SHA (`ai-evals`, `backend-quality`, `docker-build`, `edge-security`, `frontend-quality`, `release-gate`, `security-scan`)

---

## 2. Required Verification Evidence

A release candidate cannot be packaging-approved without passing the complete post-merge validation suite:

1. **Full Regression Suite**: `make check` passing cleanly (972 passed, 0 failed).
2. **Database Migrations**: Alembic upgrade head validation on PostgreSQL for both fresh databases and pre-Option-A databases. Single head (`a4b5c6d7e8f9`), 0 duplicate revisions, unbroken DAG.
3. **Dependency Audits**: Zero high/critical vulnerabilities via `pip-audit` (Python) and `npm audit --audit-level=high` (Node).
4. **Secret & Container Scanning**: Zero secrets detected via Gitleaks; zero critical container/filesystem vulnerabilities via Trivy.
5. **Software Bill of Materials**: Valid CycloneDX `sbom.json` generated and archived.
6. **Docker Stack Topology**: 7-service topology validated (`nginx`, `app`, `worker`, `postgres`, `redis`, `clamd`, `parser`). Verified network/storage isolation (ClamAV private, parser isolated on `parser_net`, worker read-only quarantine access).
7. **Document Ingestion Lifecycle**: End-to-end verification of upload states: `QUARANTINED` → `VALIDATING` → `SCANNING` → `CLEAN_PENDING_PROMOTION` → `PROMOTING` → `CLEAN` → `PARSING` → `COMPLETED`.
8. **Malware Rejection**: EICAR test fixture detected by ClamAV, document transitioned to `REJECTED_MALWARE`, deleted during teardown, zero parsing or requirement promotion.
9. **Governed Human Review**: Requirement candidates generated under `COMPLETED ExtractionRun`; ordinary user review blocked; operator CLI provisions reviewer capability (`user.can_review_requirements = True`); manual approval required to generate authoritative `Requirement`.
10. **Authentication & Session Hardening**: Argon2 password hashing, server-side Redis session storage, CSRF token enforcement, login throttling, and Nginx TLS 1.3 / security headers verified.
11. **Provider Safety**: Requirement extraction provider disabled by default (`LLM_PROVIDER=disabled` / fake fallback in test). Zero live provider calls.

---

## 3. Documentation Gate

All technical and operational documentation must match current code state:
- `README.md` updated with exact 7-service topology, upload state machine, and human review boundaries.
- `RELEASE.md` updated to distinguish automated tag workflows from Option A controlled release gate.
- `DEPLOYMENT.md` and `RUNBOOK.md` updated with pre-cutover database migration, secret file requirements, and rollback procedures.
- Known limitations explicitly documented (no MFA, no public TLS certificate issued, no production VPS target).
- Unsupported security or accuracy claims removed (no "100% accurate", "fully secure", or "universal prompt injection resistance").

---

## 4. Formal Approval States

The release gate recognizes four distinct, non-overlapping approval states:

1. **`BLOCKED`**: Validation suite incomplete, material defect detected, or unreviewed changes present.
2. **`READY FOR RELEASE-CANDIDATE REVIEW`**: Post-merge validation complete, evidence logged, pending formal sign-off.
3. **`RELEASE CANDIDATE APPROVED FOR PACKAGING`**: Release candidate signed off by Engineering, Security, and Product leads for asset generation.
4. **`PRODUCTION DEPLOYMENT AUTHORIZED`**: Separate decision requiring explicit written deployment authorization, rollback plan, and designated operator.

---

## 5. Automatic Stop Conditions

Any of the following conditions immediately halts packaging or release:
- SHA drift from target `4cdee3b`.
- Stale or failing GitHub CI check runs.
- Unresolved high or critical security vulnerability in dependencies or containers.
- Missing SBOM or SHA-256 asset checksums.
- Database migration failure or schema incompatibility.
- Undocumented environment variables or unhardened secret configuration.
- Secret exposure in repository files or log output.
- LLM provider enabled unexpectedly without explicit authorization.
- Missing rollback owner or undefined rollback target.
- Missing written sign-off from designated leads.

---

## 6. Release Controls & Packaging Constraints

- **Draft & Prerelease**: GitHub Releases must be created as **Draft** and marked **Pre-release** (not latest).
- **Asset Integrity**: Attach only verified SBOM (`sbom.json`) and source checksum manifests (`SHA256SUMS`).
- **No Automatic Deployment**: Tagging or creating a release candidate must NEVER trigger automatic VPS or production deployment.
