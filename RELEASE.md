# Branch Protection and Release Guidance

This document describes the branch protection policies, CI/CD quality gates, release process, and rollback procedures for the RFP Architect MVP.

## 1. Recommended Branch Protection Policies

To ensure stability in production and pilot environments, configure the following branch protection rules for the primary branches (`main`, `master`, and `hardening-pilot-readiness`):

1. **No Direct Commits**: Require pull requests (PR) for all changes. Direct pushes to protected branches must be disabled.
2. **Required Approvals**: Require at least one human review and approval on any PR before merge.
3. **Required Status Checks**: The following GitHub Actions checks must pass before merging:
   - `backend-quality` (formatting, linting, type checks, and pytest suite)
   - `frontend-quality` (assets build and TypeScript compilation check)
   - `ai-evals` (offline AI eval suite validation against thresholds)
   - `docker-build` (successful compilation of production container image)
   - `security-scan` (vulnerability, secret, and container scans)
4. **Conversation Resolution**: Require all conversation threads on code changes to be resolved.

---

## 2. CI/CD Quality Gates & Scanning

The repository includes a comprehensive CI pipeline in `.github/workflows/ci.yml` that executes the following checks:
- **Dependency Vulnerability Scanning**:
  - Python dependencies are validated using `pip-audit`.
  - Node dependencies are checked with `npm audit`.
- **Secret Scanning**:
  - Scans repository changes using `gitleaks` to prevent API keys or secrets exposure.
- **Container & Filesystem Scanning**:
  - filesystem and container configurations are scanned via `Trivy`.
- **Software Bill of Materials (SBOM)**:
  - Automatically generated in SPDX/CycloneDX format and uploaded as a CI release artifact.

---

## 3. Release Workflows & Option A Controlled Gate

For full release gate specifications, approval states, and stop conditions, refer to the canonical gate document:
[docs/release/A6_CONTROLLED_RELEASE_GATE.md](file:///D:/RFA/Project/rfp-architect-mvp/docs/release/A6_CONTROLLED_RELEASE_GATE.md)


### Release Paths

#### Path A: Historical Automated Tag Path
- **Trigger**: Tag matching pattern `pilot-hardening-step*` pushed to remote.
- **Behavior**: GitHub Actions `.github/workflows/release.yml` builds release assets and creates a GitHub Release.
- **Operational Risk Control**: Do **not** push `pilot-hardening-step*` tags to `main` for Option A releases to prevent unintended automated asset publishing.

#### Path B: Controlled Option A Release-Candidate Path
- **Source**: `main` at the exact candidate SHA recorded in the reviewed A6.3 evidence package.
- **Pre-conditions**: Clean working tree, 0 open PRs, full regression (`make check`) passing, 7-service Docker topology verified.
- **Release Assets**: Verified `sbom.json` and `SHA256SUMS` manifest.
- **Release Settings**: Published strictly as **Draft** and **Pre-release** (not latest).
- **Deployment Separation**: Creating a release candidate packaging asset DOES NOT perform VPS or production deployment. Formal deployment requires separate written authorization.


---

## 4. Final Release Validation

Before submitting a pull request or approving a release candidate, run the automated final validation script to verify quality gates:

```powershell
# Run validation on Windows PowerShell (skips docker build for speed if desired)
powershell -ExecutionPolicy Bypass -File scripts/final_release_validation.ps1 -SkipDockerBuild

# Run validation on Bash
./scripts/final_release_validation.sh --skip-docker-build
```

---

## 5. Rollback and Recovery Guidance

If a pilot or production release encounters a critical defect, follow the rollback procedures detailed in [RUNBOOK.md](file:///D:/RFA/Project/rfp-architect-mvp/RUNBOOK.md):

1. **Database Schema**: Execute `alembic downgrade -1` or target revision to roll back migrations safely before application code downgrade.
2. **Container Application**: Roll back deployment target to the last known stable container image tag or commit.
3. **Session Revocation**: Execute `uv run python scripts/revoke_user_sessions.py --all` if session state invalidation is required.


