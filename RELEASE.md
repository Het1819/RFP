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

## 3. Creating a Release

Releases are triggered automatically when a tag matching the pattern `pilot-hardening-step*` is pushed.

### Release Procedure
1. Create and push a tag from the verified branch:
   ```bash
   git tag -a pilot-hardening-step12 -m "Release Step 12: CI/CD Quality Gates"
   git push origin pilot-hardening-step12
   ```
2. The `Release Workflow` (`.github/workflows/release.yml`) will verify all quality gates, compile the production Docker image, generate an SBOM, compile release notes, and upload the build artifacts to GitHub Releases.

---

## 4. Rollback and Recovery Guidance

If a production/pilot deployment experiences severe failure, roll back to a previously tagged stable commit.

### Deployment Rollback
Identify the last stable pilot release tag:
- **`pilot-hardening-step8`**: Stables evidence validation, citation provenance, and snippet grounding.
- **`pilot-hardening-step9`**: Stables human review workflow, review task routing, and DOCX export approval gates.
- **`pilot-hardening-step10`**: Stables queue-backed asynchronous processing, Redis worker, and retry reliability.
- **`pilot-hardening-step11`**: Stables logging correlation, Prometheus metrics, and Pilot KPI dashboard.
- **`pilot-hardening-step12`**: Stables CI/CD validation, supply chain scanning, and local check helpers.

To roll back, deploy the corresponding container image tag or check out the tag locally and rebuild:
```bash
git checkout pilot-hardening-step11
docker build -t rfp-architect-mvp:pilot .
# Redeploy containers
```
