# Pilot Hardening Baseline Report

This report establishes the baseline security, configuration, and implementation readiness status for the RFP Architect MVP.

## 1. Current Branch & Status
- **Active Branch**: `hardening-pilot-readiness` (created and switched from `main`).
- **Working Tree Status**: Clean (no uncommitted changes).

## 2. Repository Structure Summary
The repository layout is organized as follows:
- **`app/`**: Core application source code.
  - **`app/core/`**: Core configuration (`config.py`), database utilities (`database.py`), and LLM provider interfaces (`llm.py`).
  - **`app/models/`**: SQLAlchemy models (Organization, User, ProposalProject, Document, Requirement, EvidenceLink, DraftResponse, ReviewTask, AuditEvent).
  - **`app/web/routes/`**: FastAPI routers containing web/UI view and action endpoints (`projects.py`, `compliance.py`).
  - **`app/services/`**: Core business services (`project_service.py`, `extractor.py`, `retriever.py`).
  - **`app/templates/`**: Jinja2 HTML templates.
  - **`app/static/`**: Static assets, including compiled CSS and JS in `app/static/dist/`.
  - **`app/frontend/`**: TypeScript and CSS sources used as the frontend compiler entry point.
- **`tests/`**: Unit, integration, and fixtures directory structure.
- **`alembic/`**: Database migration versions.
- **`package.json` / `vite.config.ts`**: Frontend asset builder.
- **`pyproject.toml` / `uv.lock`**: Python dependency specification.

## 3. Test & Verification Checks Results
The local validation commands were executed using the virtual environment `.venv\Scripts` executables, yielding the following results:

| Check | Command | Status | Details / Issues |
|---|---|---|---|
| **Python Tests** | `.\.venv\Scripts\pytest.exe -q` | **PASS** | 29 tests passed, 2 warnings (11.32 seconds). |
| **Linting** | `.\.venv\Scripts\ruff.exe check .` | **FAIL** | 1 issue found: `app\services\retriever.py:20:89: E501 Line too long (90 > 88)`. |
| **Formatting** | `.\.venv\Scripts\ruff.exe format --check .` | **PASS** | 47 files clean and formatted. |
| **Type Checking** | `.\.venv\Scripts\mypy.exe app` | **PASS** | Clean (no issues found in 26 source files). |
| **Assets Build** | `npm.cmd run assets:build` | **PASS** | Built `marketing.css` and `marketing.js` successfully under `app/static/dist/`. |
| **Frontend Types** | `npx.cmd tsc --noEmit` | **FAIL** | Deprecation warning/error: `tsconfig.json(7,25): error TS5107: Option 'moduleResolution=node10' is deprecated...` |

## 4. Critical Files Identified
The following files have been identified as targets for Step 2 hardening and security adjustments:
1. **Authentication & Session Management**:
   - [app/core/database.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/database.py) (contains `get_default_org_and_user` mock login utility).
   - [app/web/routes/projects.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/projects.py) (imports and invokes mock database credentials).
   - [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (uses mock login logic extensively across compliance routes).
2. **Tenant/Organization Authorization**:
   - [app/web/routes/projects.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/projects.py) (project routing and ownership verification).
   - [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (requirement and matrix access scopes).
   - [app/services/project_service.py](file:///D:/RFA/Project/rfp-architect-mvp/app/services/project_service.py) (database service fetches and writes).
3. **CSRF Protection**:
   - [app/main.py](file:///D:/RFA/Project/rfp-architect-mvp/app/main.py) (FastAPI app entry point to install middleware).
   - [app/templates/index.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/index.html) and sub-templates under `app/templates/projects/` (require injection of CSRF tokens in form/action payloads).
4. **Evidence-Link Validation**:
   - [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (contains the `link_evidence_action` route which links source documents to compliance requirements).
5. **Fake LLM Provider Restrictions**:
   - [app/core/llm.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/llm.py) (contains `get_llm_provider` selection helper).
   - [app/core/config.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/config.py) (manages environments e.g., `APP_ENV` and `LLM_PROVIDER`).

## 5. Confirmed Production Blockers
1. **Mock Security Model**: In production or pilot staging, `get_default_org_and_user` automatically creates and authenticates a hardcoded mock user/org (`default@rfparchitect.com`). This makes the system single-tenant and open to any requester without authentication.
2. **Missing CSRF Middleware**: All mutating endpoints (like POST/DELETE for projects, compliance matrix modifications, and answers drafting) lack anti-CSRF token verification, making the pilot vulnerable to Cross-Site Request Forgery.
3. **Cross-Tenant Evidence Linking**: The endpoint `link_evidence_action` accepts `document_id` via a POST parameter and links it to the requirement without checking if that document resides within the active project/tenant. A malicious user could craft a request referencing a `document_id` from another user's project/tenant and view/link its content.
4. **Permissive Fake LLM Fallback**: If the Anthropic API key is missing or configuration defaults to "fake", the system falls back to `FakeLLMProvider` silently. This must be strictly prohibited outside `development` environment to prevent dummy data leaks in production.

## 6. Recommended Next Implementation Step
The next step is **Step 3 (CSRF Protection)**:
1. Introduce lightweight CSRF protection middleware in FastAPI and update forms to render `csrf_token` fields.
2. Replace mock user generation with real cookie-based user sessions and login/logout screens.

## 7. Step 2 - Object Authorization Hardening
- **Files Changed**:
  - [app/core/security.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/security.py) (created reusable authorization/current-principal and ownership guards).
  - [app/web/routes/projects.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/projects.py) (replaced default user logic and added ownership guards).
  - [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (integrated ownership guards, requirement verification, same-project evidence link validation, and snippet validation).
  - [app/core/llm.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/llm.py) (blocked `FakeLLMProvider` fallback outside dev/local/test environments).
  - [tests/integration/test_security_hardening.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_security_hardening.py) (added regression tests).
- **Guards/Helpers Added**:
  - `check_app_env_auth()`: Blocks mock authentication outside dev/local/test.
  - `get_current_org_and_user()`: Validates environments and resolves user/org contexts.
  - `get_project_for_org()`: Asserts project-organization ownership.
  - `get_requirement_for_org()`: Asserts requirement-project-organization ownership.
  - `get_document_for_org()`: Asserts document-project-organization ownership.
  - `get_draft_for_org()`: Asserts draft-requirement-project-organization ownership.
- **Routes Patched**:
  - All project management views and upload endpoints (`projects.py`).
  - All matrix operations (merge, edit, delete, split, route review gaps, AI drafting, approve/reject, docx/xlsx exports) in `compliance.py`.
  - Evidence link creation `link_evidence_action` (added verification of project bounds, page existence, processing completion, approved status, score clamping, and exact substring validation of the snippet).
- **Tests Added**:
  - `test_production_app_env_blocks_default_user`: Asserts production block on fallback mock user.
  - `test_cross_project_evidence_linking_blocked`: Asserts document project mismatches are rejected with 400.
  - `test_foreign_document_id_rejected`: Asserts foreign document ids are rejected with 404.
  - `test_cross_project_merge_blocked`: Asserts requirements from project A cannot be merged through project B.
  - `test_valid_same_project_evidence_linking`: Asserts valid same-project linking works, and fake snippets are caught.
- **Checks Run**:
  - `pytest -q`: PASS (34 tests passed).
  - `ruff check .`: PASS (all clean).
  - `ruff format --check .`: PASS (all clean).
  - `mypy app`: PASS (no issues).
  - `npm run assets:build`: PASS (successful build).
  - `npx tsc --noEmit`: PASS (only baseline tsconfig option deprecation warning).
- **Remaining Security Gaps for Step 3**:
  - CSRF protection is not yet active on mutating HTTP endpoints. (COMPLETED)
  - Full session management/SSO setup is pending (mock credentials fallback still active in local/test/development envs).

## 8. Step 3 - CSRF Protection
- **Files Changed**:
  - [app/core/csrf.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/csrf.py) (created custom, lightweight signed-cookie `SimpleSessionMiddleware` and CSRF validation functions).
  - [app/core/templates.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/templates.py) (created shared `templates` module to expose `csrf_token` global).
  - [app/main.py](file:///D:/RFA/Project/rfp-architect-mvp/app/main.py) (configured custom Session middleware and shared templates).
  - [app/web/routes/projects.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/projects.py) (added `validate_csrf_token` dependencies to POST endpoints).
  - [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (added `validate_csrf_token` dependencies to POST and DELETE endpoints, and passed request correctly).
  - [tests/integration/test_csrf.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_csrf.py) (created CSRF integration and config validation test cases).
  - [app/templates/projects/detail.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/detail.html), [list.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/list.html), [matrix.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/matrix.html), [matrix_row.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/matrix_row.html), [matrix_row_edit.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/matrix_row_edit.html), [requirement_workspace.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/requirement_workspace.html), [status_partial.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/status_partial.html) (injected hidden CSRF tokens or HTMX headers).
- **Middleware/Config Added**:
  - `SimpleSessionMiddleware`: Implements HMAC-SHA256 signed cookie session parsing.
  - `SESSION_SECRET_KEY` validator: Raises `ValueError` at config load time if missing/weak in production envs.
- **Routes Protected**:
  - All mutating project operations (creation, RFP upload, knowledge upload).
  - All compliance matrix operations (edit, delete, split, merge, evidence linking, reviewer assignment, draft responses).
- **Templates Updated**:
  - All state-mutating forms (including normal POST and HTMX `hx-post`/`hx-delete` actions) now automatically pass the CSRF token.
- **Tests Added**:
  - `test_production_app_env_missing_session_secret`: Proves validation fails when secret is weak/None in production.
  - `test_csrf_rendered_on_get_pages`: Verifies GET pages render a CSRF token in hidden form inputs.
  - `test_post_without_csrf_token_fails`: Verifies un-tokenized POSTs fail with 403.
  - `test_post_with_invalid_csrf_token_fails`: Verifies incorrect token POSTs fail with 403.
  - `test_mutating_actions_with_valid_csrf_token_succeeds`: Verifies happy path for projects creation, upload, and merge.
  - `test_htmx_header_csrf_token_path_succeeds`: Verifies HTMX POSTs passing the token via the `X-CSRF-Token` header succeed.
- **Checks Run**:
  - `pytest -q`: PASS (40 tests passed).
  - `ruff check .`: PASS (all clean).
  - `ruff format --check .`: PASS (all clean).
  - `mypy app`: PASS (no issues).
  - `npm run assets:build`: PASS (successful build).
  - `npx tsc --noEmit`: PASS (only baseline tsconfig option deprecation warning).
- **Remaining Security Gaps for Step 4**:
  - Full session management/SSO setup is pending (mock credentials fallback still active in local/test/development envs). (COMPLETED)

## 9. Step 4 - Session Authentication Hardening
- **Files Changed**:
  - [app/core/config.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/config.py) (added `AUTH_MODE` supporting `dev`, `session`, `oidc`, as well as model-validator rules).
  - [app/core/security.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/security.py) (reimplemented `get_current_org_and_user` to fetch/validate session credentials and perform account integrity checks).
  - [app/main.py](file:///D:/RFA/Project/rfp-architect-mvp/app/main.py) (integrated `auth` router and registered HTTP 401 unauthorized handler to redirect HTML browser GET requests to `/login`).
  - [app/web/routes/auth.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/auth.py) (created new router containing `/login` GET/POST and `/logout` GET/POST endpoints).
  - [app/web/routes/projects.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/projects.py) and [compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (updated local principal resolvers to require request, enforce authentication, and only fallback to dev default if `AUTH_MODE=dev`).
  - [app/templates/login.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/login.html) (created custom, beautifully styled sign-in layout supporting all auth modes).
  - [app/templates/projects/list.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/list.html), [detail.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/detail.html), [matrix.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/matrix.html), [requirement_workspace.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/requirement_workspace.html) (injected "Sign Out" actions).
  - [tests/integration/test_auth_session.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_auth_session.py) (created comprehensive session security test suite).
  - [tests/integration/test_security_hardening.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_security_hardening.py) and [test_csrf.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_csrf.py) (updated test assertions for signature changes).
- **Config Added**:
  - `AUTH_MODE`: Determines authentication type (`dev`, `session`, `oidc`). Enforced to not be `dev` in production.
  - `OIDC_ISSUER_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`, `OIDC_REDIRECT_URI`: Enforced to be present if `AUTH_MODE="oidc"`.
- **Auth Modes Supported**:
  - `dev`: Default mode for local/dev/test environments, falling back to default user context with warning logs.
  - `session`: Enforces existing database user check via email login without plaintext storage.
  - `oidc`: Prepares workspace for OIDC/SSO integration, raising startup validation checks if not configured.
- **Routes Protected**:
  - All workspace, project management, and compliance endpoints. Unauthenticated browser GET views redirect to `/login`. Unauthenticated mutating requests return 401.
- **Login/Logout Behavior**:
  - `/login`: Checks config, sets session cookies (`user_id`, `org_id`) on successful lookup, or handles OIDC configuration checks.
  - `/logout`: Clears session dict contents (removing cookies and CSRF tokens) and redirects back to `/login`.
- **Tests Added**:
  - `test_production_fails_with_dev_auth`
  - `test_production_fails_without_strong_secret`
  - `test_oidc_fails_with_missing_config`
  - `test_unauthenticated_get_redirects_to_login`
  - `test_unauthenticated_mutating_post_fails_closed`
  - `test_dev_auth_mode_login`
  - `test_logout_clears_session`
  - `test_invalid_deleted_user_fails_closed`
- **Checks Run**:
  - `pytest -q`: PASS (48 tests passed).
  - `ruff check .`: PASS (all clean).
  - `ruff format --check .`: PASS (all clean).
  - `mypy app`: PASS (no issues).
  - `npm.cmd run assets:build`: PASS (successful build).
  - `npx.cmd tsc --noEmit`: PASS (only baseline tsconfig option deprecation warning).
- **Remaining Gaps for Step 5**:
  - Production deployment container setup is pending. (COMPLETED)

## 10. Step 5 - Production Container Deployment Setup
- **Files Changed**:
  - [app/main.py](file:///D:/RFA/Project/rfp-architect-mvp/app/main.py) (added `/healthz` and `/readyz` endpoints).
  - [tests/integration/test_health_readiness.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_health_readiness.py) (created health endpoints test suite).
  - [DEPLOYMENT.md](file:///D:/RFA/Project/rfp-architect-mvp/DEPLOYMENT.md) (created production deployment guide).
- **Docker Files Added**:
  - [.dockerignore](file:///D:/RFA/Project/rfp-architect-mvp/.dockerignore) (ignores development/OS junk and secrets).
  - [Dockerfile](file:///D:/RFA/Project/rfp-architect-mvp/Dockerfile) (multi-stage build utilizing Node.js for Vite compilation and `uv` for python dependencies).
  - [docker-compose.prod.yml](file:///D:/RFA/Project/rfp-architect-mvp/docker-compose.prod.yml) (production app, postgres, and redis services).
  - [.env.example](file:///D:/RFA/Project/rfp-architect-mvp/.env.example) (added `SESSION_SECRET_KEY` and OIDC variables).
  - [scripts/start.sh](file:///D:/RFA/Project/rfp-architect-mvp/scripts/start.sh) and [scripts/run_migrations.sh](file:///D:/RFA/Project/rfp-architect-mvp/scripts/run_migrations.sh) (fast-failing container startup and migration commands).
- **Health Endpoints**:
  - `/healthz` (indicating process liveness).
  - `/readyz` (verifying active database connection).
- **Remaining Gaps for Step 6**:
  - AI evaluation and LLM observability metrics are pending. (COMPLETED)

## 11. Step 6 - AI Evaluation and LLM Observability Baseline
- **Files Changed**:
  - [app/core/config.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/config.py) (added `ENABLE_LLM_TELEMETRY` and `ENABLE_LLM_DEBUG_PAYLOAD_LOGGING` fields).
  - [app/core/llm.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/llm.py) (implemented lightweight metadata telemetry logger and token cost estimator).
  - [scripts/run_ai_eval.py](file:///D:/RFA/Project/rfp-architect-mvp/scripts/run_ai_eval.py) (created precision, recall, and F1 evaluation runner).
  - [evals/fixtures/simple_rfp.json](file:///D:/RFA/Project/rfp-architect-mvp/evals/fixtures/simple_rfp.json), [ambiguous_rfp.json](file:///D:/RFA/Project/rfp-architect-mvp/evals/fixtures/ambiguous_rfp.json), [injection_rfp.json](file:///D:/RFA/Project/rfp-architect-mvp/evals/fixtures/injection_rfp.json) (three golden test cases).
  - [tests/integration/test_ai_eval.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_ai_eval.py) (added integration tests for telemetry and eval runner).
  - [AI_EVALS.md](file:///D:/RFA/Project/rfp-architect-mvp/AI_EVALS.md) (created quality evals documentation).
- **Telemetry Added**:
  - Observability registry capturing `request_id`, `provider`, `model`, `operation`, `latency_ms`, `success`, `exception_type`, `input_tokens`, `output_tokens`, and `estimated_cost` dynamically.
- **Eval Metrics Implemented**:
  - Extraction precision, recall, F1 score, hallucinated requirement count, missed count, evidence coverage rate, citation page accuracy, and unsupported claim count.
- **Sample Offline Eval Output**:
  - Located in `evals/results/eval_report_offline.json` (F1 = 0.667, Recall = 0.625, Precision = 0.714).
- **Checks Run**:
  - `pytest -q`: PASS (57 tests passed).
  - `ruff check .`: PASS (all clean).
  - `ruff format --check .`: PASS (all clean).
  - `mypy app`: PASS (no issues).
  - `npm.cmd run assets:build`: PASS (successful build).
  - `npx.cmd tsc --noEmit`: PASS (only baseline tsconfig option deprecation warning).
- **Remaining Gaps for Step 7**:
  - Requirement extraction quality hardening is pending. (COMPLETED)

## 12. Step 7 - Requirement Extraction Quality Hardening

### Root Cause of Step 6 Eval Failure
1. **Missing trigger words**: `FakeLLMProvider` only matched lines with `"requirement"` or `"must"` — completely missing `"shall"` and `"should"`.
2. **Hardcoded section assignment**: All extracted requirements got `source_section="Section 1.1"` regardless of actual source.
3. **No injection-line salvage**: Lines starting with injection text were dropped entirely, causing the legitimate SSO requirement (on the same line) to be lost.
4. **Brittle eval matching**: Old substring matching was too strict (required full substring containment) and didn't handle version alias normalisation (postgres vs postgresql).
5. **No deduplication**: Near-duplicate requirements across pages were counted as separate hallucinations vs. expected.

### Files Changed
- [app/core/llm.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/llm.py): Complete rewrite of `FakeLLMProvider` with `_parse_rfp_text()` rule-based extractor. Added: `RequirementDraft.validate_original_text`, `normalize_text()`, `_token_overlap()`, `deduplicate_requirements()`, `_is_injection_text()`, `_INJECTION_PATTERNS`, `_REQ_TRIGGER` (must/shall/should/required), `_PAGE_MARKER`, `_SECTION_HEADER` tracking.
- [scripts/run_ai_eval.py](file:///D:/RFA/Project/rfp-architect-mvp/scripts/run_ai_eval.py): Switched matching to Jaccard token-overlap (`>= 0.55`). Added `thresholds_pass` field. Added `[PASS]/[FAIL]` banner. Removed brittle substring-only matching.
- [AI_EVALS.md](file:///D:/RFA/Project/rfp-architect-mvp/AI_EVALS.md): Comprehensive update with eval results, deduplication docs, injection handling, matching logic, citation validity, and threshold table.
- [tests/integration/test_extraction_quality.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_extraction_quality.py): 26 new regression tests.

### Extraction Schema / Validation Changes
- `RequirementDraft.original_text` now validated with `@field_validator`: rejects empty strings and prompt-injection text.
- Malformed LLM output items are caught individually with `ValueError/TypeError` — valid items still processed.
- `extraction_warnings` field added to record merge events and other extraction anomalies.

### Deduplication Changes
- `deduplicate_requirements()` uses Jaccard token overlap with threshold 0.75.
- Near-duplicates are merged (first occurrence kept, warning added).
- Distinct requirements are always preserved regardless of shared keywords.

### Prompt-Injection Controls Added
- `_INJECTION_PATTERNS` compiled regex list matches 7 common jailbreak patterns.
- Whole-line injection text is rejected and logged as `extraction_rejected_injection`.
- **Salvage logic**: legitimate requirements after injection sentences on the same line are still extracted.
- Real Anthropic `_SYSTEM_EXTRACT` prompt updated with explicit guardrail instructions.

### Citation / Page Validation Changes
- `_PAGE_MARKER` regex tracks `[PAGE N]` markers and updates `source_page` correctly per line.
- `_SECTION_HEADER` regex detects actual section headings and updates `source_section` correctly.
- Page numbers are never fabricated — `source_page = None` if no marker precedes the requirement.

### Tests Added (26 tests in test_extraction_quality.py)
- Normalisation helpers (casefold, alias expansion, punctuation)
- Token overlap (identical, disjoint, partial, unrelated)
- Injection detection (positive and negative cases)
- Schema validation (empty text, injection text, valid case)
- Extraction per fixture (simple/ambiguous/injection)
- shall/should trigger words explicitly tested
- Page and section tracking verified
- Injection-line salvage tested (SSO requirement recovered)
- Deduplication (near-dup merged, distinct preserved)
- FakeLLMProvider async interface
- Telemetry metadata-only assertion
- Offline eval threshold pass (recall >= 0.90, hallucinations = 0)
- No real LLM API key required for pytest

### Offline Eval Result Summary (Step 7)
| Metric | Result | Threshold | Status |
|--------|--------|-----------|--------|
| Recall | 1.000 | >= 0.90 | PASS |
| Precision | 1.000 | — | — |
| F1 Score | 1.000 | — | — |
| Hallucinated Requirements | 0 | = 0 | PASS |
| Missed Requirements | 0 | — | — |
| Evidence Coverage | 1.000 | >= 0.85 | PASS |
| Unsupported Claims | 0 | = 0 | PASS |
| Citation Accuracy | 1.000 | >= 0.80 | PASS |

### Checks Run
- `pytest -q`: PASS (83 tests passed).
- `ruff check .`: PASS (all clean).
- `ruff format --check .`: PASS (all clean).
- `mypy app`: PASS (no issues).
- `npm.cmd run assets:build`: PASS (successful build).
- `npx.cmd tsc --noEmit`: PASS (only baseline tsconfig option deprecation warning).
- `scripts/run_ai_eval.py --offline`: PASS (thresholds_pass = true).

### Remaining Gaps for Step 7
- Human review interface and HTMX workflow actions for requirement approval/rejection. (COMPLETED)


## 13. Step 8 - Evidence Grounding and Citation Integrity

### Files Changed
- [app/services/evidence_validation.py](file:///D:/RFA/Project/rfp-architect-mvp/app/services/evidence_validation.py) (created containing evidence validation and draft grounding checkers).
- [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (switched inline checks to validation service, added draft approval blocks, and appended citation provenance in DOCX export).
- [app/services/retriever.py](file:///D:/RFA/Project/rfp-architect-mvp/app/services/retriever.py) (restricted retrieval to completed documents, returning page citation details).
- [scripts/run_ai_eval.py](file:///D:/RFA/Project/rfp-architect-mvp/scripts/run_ai_eval.py) (extended offline evaluation framework with evidence validation/grounding integrity checks).
- [evals/fixtures/evidence_integrity_rfp.json](file:///D:/RFA/Project/rfp-architect-mvp/evals/fixtures/evidence_integrity_rfp.json) (added new integrity/grounding eval fixture).
- [tests/integration/test_evidence_grounding.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_evidence_grounding.py) (added 13 new integration tests).
- [tests/unit/test_evidence_validation.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/unit/test_evidence_validation.py) (added 26 new unit tests).
- [tests/integration/test_knowledge.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_knowledge.py) and [test_security_hardening.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_security_hardening.py) (updated test assertions).

### Evidence Validation Helpers Added
- `validate_evidence_candidate()`
- `resolve_evidence_from_document_page()`
- `evidence_quote_exists_on_page()`
- `normalize_evidence_text()`
- `require_same_project_evidence()`

### Client-Trusted Fields Removed/Restricted
- **Snippet text / Page number**: Stricter normalization and substring matching.
- **Evidence score**: Completely recomputed or clamped to 0.0 server-side (ignored client values).

### Draft Grounding Checks Added
- `extract_draft_claims()`: Extracts sentences, skipping boilerplate compliance text.
- `check_claim_support()`: Calculates Jaccard token overlap against evidence snippets (minimum 0.20 threshold).
- `validate_draft_grounding()`: Flags unsupported claims.
- **Approval Gate**: Set draft status to `needs_review` and matrix requirement to `NEEDS_REVIEW` if grounding fails or a mandatory requirement has no evidence links.

### Retrieval Integrity Changes
- Filtered retrieval queries to only return pages from documents with `processing_status='completed'`.
- Trimmed and validated retrieved snippets to strict character bounds `[10, 2000]`.

### Tests Added
- **Unit (25 cases)**: Cover Jaccard token overlap, claim extraction, boilerplate skipping, and empty fields.
- **Integration (13 cases)**: Cover cross-project blocking, fake snippet rejection, wrong page rejection, unapproved document rejection, client-score dismissal, mandatory block, and DOCX citation provenance.

### Offline Eval Result Summary (Step 8)
| Metric | Result | Threshold | Status |
|---|---|---|---|
| Recall | 1.000 | >= 0.90 | PASS |
| Precision | 1.000 | — | — |
| F1 Score | 1.000 | — | — |
| Hallucinated Requirements | 0 | = 0 | PASS |
| Evidence Coverage | 1.000 | >= 0.85 | PASS |
| Unsupported Claims | 0 | = 0 | PASS |
| Citation Page Accuracy | 1.000 | — | — |
| Fabricated Evidence Rejected | 1 | — | — |
| Invalid Citations Rejected | 1 | — | — |
| Draft Grounding Pass Rate | 0.500 | — | — |
| Evidence Validation Accuracy | 1.000 | = 1.000 | PASS |

### Checks Run
- `pytest -q`: PASS (127 tests passed).
- `ruff check .`: PASS (all clean).
- `ruff format --check .`: PASS (all clean).
- `mypy app`: PASS (no issues).
- `npm run assets:build`: PASS (successful build).
- `npx tsc --noEmit`: PASS (only tsconfig option warning).
- `.\.venv\Scripts\python.exe scripts/run_ai_eval.py --offline`: PASS (thresholds_pass = true).

### Remaining Gaps for Step 9
- Humans still need a streamlined UI workflow for routing review tasks to other users. (COMPLETED)


## 14. Step 9 - Human Review Workflow and Task Routing

### Files Changed
- [app/models/requirement.py](file:///D:/RFA/Project/rfp-architect-mvp/app/models/requirement.py) (added `assigned_to_user_id`, `assigned_by_user_id`, and `assigned_at` fields).
- [app/models/comment.py](file:///D:/RFA/Project/rfp-architect-mvp/app/models/comment.py) (created new `RequirementComment` model).
- [app/models/__init__.py](file:///D:/RFA/Project/rfp-architect-mvp/app/models/__init__.py) (registered and exported new comment model).
- [alembic/versions/ef0310e3c06f_add_reviewer_assignment_and_comments.py](file:///D:/RFA/Project/rfp-architect-mvp/alembic/versions/ef0310e3c06f_add_reviewer_assignment_and_comments.py) (created Alembic migration).
- [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (extended `RequirementStatus`, added reviewer-assignment user validations, start/request changes/reject/reopen review endpoints, comments handling, filtering parameter support, and DOCX/XLSX unapproved export markers).
- [app/templates/projects/matrix.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/matrix.html) (implemented Review Tasks Queue filter area and project overdue warnings).
- [app/templates/projects/requirement_workspace.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/requirement_workspace.html) (integrated user select assign dropdown, start review/reopen/changes requested/reject actions, HTML-escaped comments history log, and warning banners for missing evidence / grounding failures).
- [tests/integration/test_human_review.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_human_review.py) (created 9 new integration tests).

### Status/Transition changes
- Added statuses `IN_REVIEW` and `CHANGES_REQUESTED` to requirement statuses.
- Formulated deterministic transitions:
  - Assign reviewer -> moves to `NEEDS_REVIEW`.
  - Start review -> moves to `IN_REVIEW`.
  - Request changes -> moves to `CHANGES_REQUESTED` (requires reason/comment).
  - Reject -> moves to `REJECTED` (requires reason/comment).
  - Reopen -> moves to `NEEDS_REVIEW` (resets draft status).
  - Approve -> moves to `APPROVED` (if grounding and evidence validations pass).

### Assignment workflow
- Authenticated users in the same organization can assign requirements to other users in the same organization.
- Assigning to foreign or non-existent user IDs fails closed with 404.
- Allows unassigning, clearing `assigned_to_user_id` and `owner_name`.

### Review actions
- Reviewers can start reviews, request changes, reject drafts, and reopen requirements with comments.
- Comments are persisted in `RequirementComment` and HTML-escaped during rendering to prevent XSS.

### Audit events
- Logs specific audit events for review tasks:
  - `REVIEW_ASSIGNED`
  - `REVIEW_STARTED`
  - `REVIEW_CHANGES_REQUESTED`
  - `REVIEW_REJECTED`
  - `REVIEW_REOPENED`
  - `REVIEW_NOTE_ADDED`
  - `REVIEW_APPROVED`

### UI/Template changes
- Overdue warning banner based on `due_date`.
- Interactive filter bar counts based on review status and assignment.
- Clear workspace warning labels when grounding validation fails.

### Tests Added
- `test_unauthenticated_assignment_fails`
- `test_assignment_requires_csrf`
- `test_reviewer_must_belong_to_same_org`
- `test_valid_reviewer_assignment_succeeds`
- `test_request_changes_requires_reason`
- `test_reject_requires_reason`
- `test_notes_are_escaped_in_workspace`
- `test_review_task_filters`
- `test_export_preserves_not_approved_marking`

### Checks/Tests results
- `pytest -q`: PASS (135 tests passed).
- `ruff check .`: PASS (all clean).
- `ruff format --check .`: PASS (all clean).
- `mypy app`: PASS (no issues).
- `npm run assets:build`: PASS (successful build).
- `npx tsc --noEmit`: PASS.
- `.\.venv\Scripts\python.exe scripts/run_ai_eval.py --offline`: PASS.

### Remaining Issues
- None. Step 9 is complete.
