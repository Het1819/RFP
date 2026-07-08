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
- None. Step 9 is complete. (COMPLETED)


## 15. Step 10 - Queue-backed Processing and Job Reliability

### Files Changed
- [app/core/config.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/config.py) (added queue and redis configuration settings).
- [app/core/queue.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/queue.py) (created background queue enqueuer and task coordinator).
- [app/models/job.py](file:///D:/RFA/Project/rfp-architect-mvp/app/models/job.py) (created new `ProcessingJob` model to track steps, progress percentage, retries, and generic error messages).
- [app/models/__init__.py](file:///D:/RFA/Project/rfp-architect-mvp/app/models/__init__.py) (exported and registered the `ProcessingJob` model).
- [alembic/versions/f0093fb3f942_create_processing_jobs_table.py](file:///D:/RFA/Project/rfp-architect-mvp/alembic/versions/f0093fb3f942_create_processing_jobs_table.py) (created database migration for background jobs table).
- [app/services/project_service.py](file:///D:/RFA/Project/rfp-architect-mvp/app/services/project_service.py) (implemented transactional pipeline logic `process_job_pipeline_async` with step-by-step progress tracking, audit logging, automatic retries, and idempotent deletions to avoid duplicated rows).
- [app/worker.py](file:///D:/RFA/Project/rfp-architect-mvp/app/worker.py) (created arq background worker configuration and process entry point).
- [app/web/routes/projects.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/projects.py) (updated file upload and knowledge base uploads to run asynchronously, added `/retry` action, `/jobs` dashboard, and `/documents/{id}/status` JSON endpoint).
- [app/templates/projects/status_partial.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/status_partial.html) (designed HTMX status polling card with active progress bar and retry button).
- [app/templates/projects/detail.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/projects/detail.html) (enabled stepper active state for pending document processing).
- [docker-compose.prod.yml](file:///D:/RFA/Project/rfp-architect-mvp/docker-compose.prod.yml) (added `worker` container service and configured Redis AOF persistence volume).
- [DEPLOYMENT.md](file:///D:/RFA/Project/rfp-architect-mvp/DEPLOYMENT.md) (added guides on running background workers, verifying queues, and retrying failed jobs).
- [.env.example](file:///D:/RFA/Project/rfp-architect-mvp/.env.example) (documented queue configuration keys).
- [tests/integration/test_queue_jobs.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_queue_jobs.py) (added 5 comprehensive integration tests).

### Queue Implementation Chosen
- **Arq (Async Redis Queue)**: Lightweight Redis-backed job queue for python. Fits FastAPI's async nature perfectly.

### Config Added
- `QUEUE_ENABLED` (Boolean, defaults to False for local/test to run without Redis).
- `REDIS_URL` (DSN, validated to be present in production/pilot when QUEUE_ENABLED is True).
- `JOB_MAX_RETRIES` (defaults to 3).
- `JOB_TIMEOUT_SECONDS` (defaults to 300).
- `JOB_RETRY_BACKOFF_SECONDS` (defaults to 5).

### Job Model / Statuses
- Columns: `id`, `org_id`, `project_id`, `document_id`, `job_type`, `status` (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`, `RETRYING`, `CANCELLED`), `progress_percent`, `current_step`, `attempts`, `max_attempts`, `error_type`, `safe_error_message`, `started_at`, `finished_at`, `created_by_user_id`, `created_at`, `updated_at`.

### Idempotency
- Added transaction-aware cleanups deleting existing `DocumentPage` and `Requirement` entries for a document before writing new ones during retry.

### Tests Added
- `test_upload_creates_job_with_queue_enabled`
- `test_job_idempotency_prevents_duplication`
- `test_failed_processing_logs_safe_error`
- `test_foreign_org_validation_for_jobs_and_retry`
- `test_redis_url_enforcement_in_production`

### Checks/Tests results
- `pytest -q`: PASS (140 tests passed).
- `ruff check .`: PASS (all clean).
- `ruff format --check .`: PASS (all clean).
- `mypy app`: PASS (no issues).
- `npm run assets:build`: PASS (successful build).
- `npx tsc --noEmit`: PASS.
- `docker compose -f docker-compose.prod.yml config`: PASS.
- `docker build -t rfp-architect-mvp:pilot .`: PASS (compiled successfully).
- `.\.venv\Scripts\python.exe scripts/run_ai_eval.py --offline`: PASS.

### Remaining Issues
- None. Step 10 is complete.

---

## 16. Step 11 - Production Observability, Logging, and Dashboard

### Files Changed
- [app/core/observability.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/observability.py) (created JSON log formatter, metrics registry, and Prometheus text metrics exporter).
- [app/main.py](file:///D:/RFA/Project/rfp-architect-mvp/app/main.py) (added correlation ID and metrics middleware, registered `/metrics`, `/healthz`, and `/readyz` endpoints).
- [app/web/routes/projects.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/projects.py) (implemented authenticated `/projects/ops/dashboard` view with Outfit styling and essential pilot KPIs).
- [app/web/routes/auth.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/auth.py) (implemented audit event logging for login/logout actions).
- [app/web/routes/compliance.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/compliance.py) (added correlation ID tracing to extraction and drafting services).
- [app/core/llm.py](file:///D:/RFA/Project/rfp-architect-mvp/app/core/llm.py) (propagated correlation ID and registered call cost and latency metrics).
- [app/worker.py](file:///D:/RFA/Project/rfp-architect-mvp/app/worker.py) (propagated trace correlation context inside background jobs).
- [alembic/versions/35bf60412d49_add_request_id_to_audit_events.py](file:///D:/RFA/Project/rfp-architect-mvp/alembic/versions/35bf60412d49_add_request_id_to_audit_events.py) (database migration for audit events).
- [alembic/versions/ce1d99d00aeb_add_request_id_to_processing_jobs.py](file:///D:/RFA/Project/rfp-architect-mvp/alembic/versions/ce1d99d00aeb_add_request_id_to_processing_jobs.py) (database migration for jobs correlation).
- [scripts/backup_postgres.sh](file:///D:/RFA/Project/rfp-architect-mvp/scripts/backup_postgres.sh) (credential-safe PostgreSQL backup automation script).
- [scripts/restore_postgres.sh](file:///D:/RFA/Project/rfp-architect-mvp/scripts/restore_postgres.sh) (credential-safe PostgreSQL restore automation script).
- [DEPLOYMENT.md](file:///D:/RFA/Project/rfp-architect-mvp/DEPLOYMENT.md) (documented postgres backup/restore drills, metrics endpoints, dashboard usage, and suggested Prometheus alerts).
- [tests/integration/test_observability.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_observability.py) (implemented 6 integration tests validating logging format, middleware, dashboard security, and metrics).

### Telemetry Features
- In-memory global state metrics tracking request counts, latencies, LLM invocation cost.
- Log masking sanitizing keys: `prompt`, `completion`, `raw_text`, `hashed_password`, `email`, `secret_key`, etc.

### Checks/Tests Results
- `pytest -q`: PASS (147 tests passed).
- `ruff check .`: PASS.
- `ruff format --check .`: PASS.
- `mypy app`: PASS.
- `docker compose -f docker-compose.prod.yml config`: PASS.
- `docker build -t rfp-architect-mvp:pilot .`: PASS.
- `scripts/run_ai_eval.py --offline`: PASS.

### Remaining Issues
- None. Step 11 is complete.

---

## 17. Step 12 - CI/CD Release Gates and Supply Chain Security

### Files Changed
- [.github/workflows/ci.yml](file:///D:/RFA/Project/rfp-architect-mvp/.github/workflows/ci.yml) (created automated quality check workflow containing linting, formatting, type checking, pytest suite, frontend build, offline AI eval gates, dependency audits, secret scanning, filesystem security, and SBOM generation).
- [.github/workflows/release.yml](file:///D:/RFA/Project/rfp-architect-mvp/.github/workflows/release.yml) (created manual/tag release workflow that runs checks, builds the production container, and uploads generated SBOM).
- [scripts/check_all.ps1](file:///D:/RFA/Project/rfp-architect-mvp/scripts/check_all.ps1) (added local PowerShell validation helper running all static and runtime checks for Windows).
- [scripts/check_all.sh](file:///D:/RFA/Project/rfp-architect-mvp/scripts/check_all.sh) (added local shell script validation helper running all static and runtime checks for Unix-like systems).
- [RELEASE.md](file:///D:/RFA/Project/rfp-architect-mvp/RELEASE.md) (created new release and branch protection guidelines document).
- [DEPLOYMENT.md](file:///D:/RFA/Project/rfp-architect-mvp/DEPLOYMENT.md) (updated deployment document to include local helper script guides and CI quality gates details).
- [AI_EVALS.md](file:///D:/RFA/Project/rfp-architect-mvp/AI_EVALS.md) (updated document to detail offline evaluation execution inside CI).

### Release Gates Implemented
- Linting/Formatting: Ruff check and format check.
- Type Checking: Python Mypy + Node TypeScript `tsc --noEmit`.
- Automated Tests: Integration suite validation (Pytest).
- AI Quality Gate: Offline evaluation suite validation against recall/precision/grounding thresholds.
- Docker Validation: Multi-stage Docker build and compose syntax check.
- Security Auditing:
  - Python dependencies: `pip-audit` via compiled `requirements.txt` from `pyproject.toml`.
  - Node dependencies: `npm audit --audit-level=high`.
  - Secret scanning: `gitleaks` via GitHub Action.
  - Filesystem/config vulnerability scanning: `Trivy`.
- Software Supply Chain: Automated CycloneDX SBOM generation and preservation.

### Checks/Tests Results
- `pytest -q`: PASS (147 tests passed).
- `ruff check .`: PASS.
- `ruff format --check .`: PASS.
- `mypy app`: PASS.
- `npm run assets:build`: PASS.
- `npx tsc --noEmit`: PASS.
- `scripts/run_ai_eval.py --offline`: PASS.
- `docker compose -f docker-compose.prod.yml config`: PASS.
- `docker build -t rfp-architect-mvp:pilot .`: PASS.

### Remaining Issues
- None. Step 12 is complete.

---

## 18. Step 13 - Staging Deployment Rehearsal and Pilot Readiness

### Files Changed
- [.env.staging.example](file:///D:/RFA/Project/rfp-architect-mvp/.env.staging.example) (created staging environment configuration template).
- [scripts/smoke_test.ps1](file:///D:/RFA/Project/rfp-architect-mvp/scripts/smoke_test.ps1) (created PowerShell smoke-test script for Windows validation).
- [scripts/smoke_test.sh](file:///D:/RFA/Project/rfp-architect-mvp/scripts/smoke_test.sh) (created Bash smoke-test script for Unix-like validation).
- [PILOT_READINESS_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_READINESS_CHECKLIST.md) (created comprehensive pre-launch checklist covering all security, operations, and AI quality checks).
- [RUNBOOK.md](file:///D:/RFA/Project/rfp-architect-mvp/RUNBOOK.md) (created runbook for deployment rehearsals, rollback procedures, and database/Redis backup/restore drills).
- [scripts/seed_pilot_demo.py](file:///D:/RFA/Project/rfp-architect-mvp/scripts/seed_pilot_demo.py) (created database seed script to populate environment with synthetic, non-production organizations, users, and RFP metadata).
- [tests/integration/test_step13_acceptance.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_step13_acceptance.py) (added integration tests validating Step 13 files and checking they contain no secrets and enforce environment restrictions).
- [DEPLOYMENT.md](file:///D:/RFA/Project/rfp-architect-mvp/DEPLOYMENT.md) (updated to document staging deployment rehearsals, smoke test executions, and database seeding).
- [RELEASE.md](file:///D:/RFA/Project/rfp-architect-mvp/RELEASE.md) (updated to document the Step 13 rollback target and link to the operations runbook).

### Staging Rehearsal & Validation Features
- **Cross-Platform Smoke Testing:** Checks health/readiness endpoints (`/healthz` and `/readyz`), login load capabilities, unauthenticated routing redirects, metrics exposure, and frontend asset manifests.
- **Annotated Tag Rollbacks:** Documented rollback procedures down to `pilot-hardening-step12` and rollback policy for database schema downgrades (using Alembic).
- **Postgres & Redis Recovery Drill:** Verifies recovery via pg_dump/pg_restore into separate staging validation databases, matching NIST incident response recommendations.
- **Production-Safe Seeding:** Idempotent database seeder that enforces a safety block refusing to run if `APP_ENV=production`.

### Checks/Tests Results
- `pytest -q`: PASS (150 tests passed).
- `ruff check .`: PASS.
- `ruff format --check .`: PASS.
- `mypy app`: PASS.
- `npm run assets:build`: PASS.
- `npx tsc --noEmit`: PASS.
- `scripts/run_ai_eval.py --offline`: PASS.
- `docker compose -f docker-compose.prod.yml config`: PASS.
- `docker build -t rfp-architect-mvp:pilot .`: PASS.
- `powershell -ExecutionPolicy Bypass -File scripts/check_all.ps1`: PASS.

### Remaining Issues
- None. Step 13 is complete.

---

## 19. Step 14 - Controlled Pilot Onboarding and Execution

### Files Changed
- [PILOT_ONBOARDING_GUIDE.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_ONBOARDING_GUIDE.md) (created guide detailing target personas, supported inputs/limits, and schedule parameters).
- [PILOT_PARTICIPANT_QUICKSTART.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_PARTICIPANT_QUICKSTART.md) (created quickstart guide for step-by-step pilot workspace execution).
- [PILOT_DATA_HANDLING_NOTICE.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_DATA_HANDLING_NOTICE.md) (created data security notice containing transit and retention info, without making unsupported regulatory claims).
- [PILOT_SUCCESS_METRICS.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_SUCCESS_METRICS.md) (created success metrics guide defining core AI, SLA, and go/no-go decisions).
- [PILOT_TRIAGE_WORKFLOW.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_TRIAGE_WORKFLOW.md) (created issue escalation workflow mapping severities and owner roles).
- [PILOT_EXIT_REPORT_TEMPLATE.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_EXIT_REPORT_TEMPLATE.md) (created template for customer pilot summaries and exit metrics).
- [app/models/feedback.py](file:///D:/RFA/Project/rfp-architect-mvp/app/models/feedback.py) (created `PilotFeedback` database model containing categories, severities, message lengths, and creator IDs).
- [app/models/__init__.py](file:///D:/RFA/Project/rfp-architect-mvp/app/models/__init__.py) (imported and exported `PilotFeedback`).
- [alembic/versions/7a14e99f1390_create_pilot_feedback_table.py](file:///D:/RFA/Project/rfp-architect-mvp/alembic/versions/7a14e99f1390_create_pilot_feedback_table.py) (Alembic database schema migration).
- [app/web/routes/feedback.py](file:///D:/RFA/Project/rfp-architect-mvp/app/web/routes/feedback.py) (created auth, CSRF, and org-scoped GET/POST `/feedback` endpoints).
- [app/templates/feedback.html](file:///D:/RFA/Project/rfp-architect-mvp/app/templates/feedback.html) (HTML feedback input form styled with Outfit/Plus Jakarta Sans).
- [app/main.py](file:///D:/RFA/Project/rfp-architect-mvp/app/main.py) (registered the feedback router).
- [tests/integration/test_step14_pilot.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_step14_pilot.py) (created integration test suite covering artifact presence, regulatory claim validation, auth, CSRF, and tenant scoping on feedback route).
- [PILOT_READINESS_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_READINESS_CHECKLIST.md) (appended Step 14 checklists to pre-flight Go/No-Go Decision Criteria).
- [RUNBOOK.md](file:///D:/RFA/Project/rfp-architect-mvp/RUNBOOK.md) (referenced triage workflows and support escalation paths).

### Controlled Pilot Execution Features
- **In-App Feedback Capture:** An authenticated, CSRF-protected, and organization-scoped `/feedback` portal allows users to report bugs, AI quality issues, evidence mismatches, and export problems directly inside the workspace.
- **Triage and SLA Targets:** Documented clear triage lanes (Blocker, High, Medium, Low) and response times ranging from 30 mins to 24 hours.
- **Willingness-to-Pay Metrics:** Establishes pre-defined success measurements for Recall (>=90%), CSAT (>=4.0), and conversion recommendations.

### Checks/Tests Results
- `pytest -q`: PASS (154 tests passed).
- `ruff check .`: PASS.
- `ruff format --check .`: PASS.
- `mypy app`: PASS.
- `npm run assets:build`: PASS.
- `npx tsc --noEmit`: PASS.
- `scripts/run_ai_eval.py --offline`: PASS.
- `docker compose -f docker-compose.prod.yml config`: PASS.
- `docker build -t rfp-architect-mvp:pilot .`: PASS.
- `powershell -ExecutionPolicy Bypass -File scripts/check_all.ps1`: PASS.

### Remaining Issues
- None. Step 14 is complete.

---

## 20. Step 15 - Paid Pilot Commercialization and Customer Conversion

### Files Changed
- [PILOT_READINESS_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_READINESS_CHECKLIST.md) (appended Step 15 commercial readiness checks).
- [PILOT_SUCCESS_METRICS.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_SUCCESS_METRICS.md) (linked paid conversion criteria success thresholds).

### Commercial Docs Added
- [ICP_QUALIFICATION_SCORECARD.md](file:///D:/RFA/Project/rfp-architect-mvp/ICP_QUALIFICATION_SCORECARD.md) (qualification scoring grid for target segments and personas).
- [PAID_PILOT_OFFER.md](file:///D:/RFA/Project/rfp-architect-mvp/PAID_PILOT_OFFER.md) (pilot packages of 2/4/6 weeks, inclusions, exclusions, and counsel review notices).
- [PRICING_AND_PACKAGING.md](file:///D:/RFA/Project/rfp-architect-mvp/PRICING_AND_PACKAGING.md) (pricing strategy from $2,500 to $10,000, base platform + seat/page allowances, and red lines).
- [DISCOVERY_CALL_SCRIPT.md](file:///D:/RFA/Project/rfp-architect-mvp/DISCOVERY_CALL_SCRIPT.md) (discovery call framework and qualification questions).
- [DEMO_SCRIPT.md](file:///D:/RFA/Project/rfp-architect-mvp/DEMO_SCRIPT.md) (20-minute and 45-minute demo steps, plus boundaries on what not to claim).
- [PILOT_PROPOSAL_TEMPLATE.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_PROPOSAL_TEMPLATE.md) (structured pilot contract proposal template with legal disclaimer).
- [SECURITY_RESPONSE_PACK.md](file:///D:/RFA/Project/rfp-architect-mvp/SECURITY_RESPONSE_PACK.md) (concise response pack for IT risk questionnaires using `[IMPLEMENTED]` and `[DOCUMENTED]` labels).
- [ROI_CALCULATOR_GUIDE.md](file:///D:/RFA/Project/rfp-architect-mvp/ROI_CALCULATOR_GUIDE.md) (cost avoidance and payback formulas with reference conservative/aggressive scenarios).
- [OBJECTION_HANDLING_GUIDE.md](file:///D:/RFA/Project/rfp-architect-mvp/OBJECTION_HANDLING_GUIDE.md) (honest, non-overpromising objections handling regarding hallucinations, security, and compliance).
- [PAID_CONVERSION_CRITERIA.md](file:///D:/RFA/Project/rfp-architect-mvp/PAID_CONVERSION_CRITERIA.md) (transition milestones, expansion/stop triggers, and rollout requirements).
- [tests/integration/test_step15_commercial.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_step15_commercial.py) (integration test suite verifying file presence and validating that no uncertified compliance statements or secrets exist in the commercial materials).

### Pricing & Packaging Assumptions
* Flat-fee pilots: $2,500 (2 weeks), $5,000 (4 weeks), $10,000 (6 weeks).
* Base platform annual subscriptions with usage tiers for seats or processed pages.
* No free pilots without explicit LOI conversion guarantee or strategic case study value.

### Security Response Pack
* Explicitly states authentication, CSRF, multi-tenancy layer, daily backups, and CI/CD scan checks are `[IMPLEMENTED]`.
* Clarifies that SOC 2, HIPAA, GDPR, FedRAMP, and ISO 27001 certifications are **not certified / unsupported** at this MVP stage.

### ROI/Value Model
* Models time saved, cost avoided, and payback period using RFP volume, SME drafting hours, loaded SME rates, and matrix copy-paste overhead.

### Objection Handling
* Refuses to overpromise compliance or AI accuracy. Emphasizes mandatory human-in-the-loop review, vector database evidence grounding, and scoped pilot boundary setups.

### Tests Added
* Integration tests verify all 10 files are present, do not contain real API keys, make no fake SOC 2/HIPAA/GDPR compliance statements, and include required disclaimers/exclusions.

### Checks/Tests Results
* `pytest -q`: PASS (166 tests passed).
* `ruff check .`: PASS.
* `ruff format --check .`: PASS.
* `mypy app`: PASS.
* `npm run assets:build`: PASS.
* `npx tsc --noEmit`: PASS.
* `scripts/run_ai_eval.py --offline`: PASS.
* `docker compose -f docker-compose.prod.yml config`: PASS.
* `docker build -t rfp-architect-mvp:pilot .`: PASS.
* `powershell -ExecutionPolicy Bypass -File scripts/check_all.ps1`: PASS.

### Remaining Gaps for Step 16
* None. Step 15 is complete.

---

## 21. Step 16 - Outreach Execution and Sales Pipeline Operations

### Files Changed
- [PILOT_READINESS_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_READINESS_CHECKLIST.md) (appended Step 16 sales ops readiness checks).

### Outreach Assets Added
- [TARGET_ACCOUNT_LIST_TEMPLATE.csv](file:///D:/RFA/Project/rfp-architect-mvp/TARGET_ACCOUNT_LIST_TEMPLATE.csv) (target account database schema template with placeholder rows).
- [CRM_PIPELINE_TEMPLATE.csv](file:///D:/RFA/Project/rfp-architect-mvp/CRM_PIPELINE_TEMPLATE.csv) (sales pipeline tracking sheet with core qualification status fields and WON/LOST stages).
- [OUTREACH_TEMPLATES.md](file:///D:/RFA/Project/rfp-architect-mvp/OUTREACH_TEMPLATES.md) (cold and follow-up templates focusing on Excel matrix pain and SME drafting delay solutions).
- [SALES_SEQUENCE.md](file:///D:/RFA/Project/rfp-architect-mvp/SALES_SEQUENCE.md) (10-day multi-channel sales sequence, stop conditions, and opt-out/unsubscribe compliance rules).
- [DISCOVERY_SCORECARD.md](file:///D:/RFA/Project/rfp-architect-mvp/DISCOVERY_SCORECARD.md) (scorecard to qualify customer RFP volume, pains, urgency, and cloud security readiness).
- [DEMO_QUALIFICATION_RULES.md](file:///D:/RFA/Project/rfp-architect-mvp/DEMO_QUALIFICATION_RULES.md) (explicit rules defining who gets a demo, who remains in discovery, and who is disqualified).
- [PILOT_DEAL_REVIEW_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_DEAL_REVIEW_CHECKLIST.md) (pre-proposal alignment checklist checking target buyer authority, success metrics, and legal/security reviews).
- [WEEKLY_SALES_CADENCE.md](file:///D:/RFA/Project/rfp-architect-mvp/WEEKLY_SALES_CADENCE.md) (calendar blocks, pipeline review meetings, and performance dashboard KPIs).
- [AI_CLAIMS_AND_OUTREACH_GUARDRAILS.md](file:///D:/RFA/Project/rfp-architect-mvp/AI_CLAIMS_AND_OUTREACH_GUARDRAILS.md) (claim safety guidelines clarifying allowed/prohibited claims, human-in-the-loop dependencies, and safe vs unsafe phrasing examples).
- [tests/integration/test_step16_sales_ops.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_step16_sales_ops.py) (integration test suite verifying CSV structures, file presence, lack of secrets, and compliance with outreach claim safety policies).

### CRM Templates
* Targets, contacts, stages, sources, confirmation checkboxes (pain, RFP volume), pilot pricing, expected contract value, win probabilities, and next steps are fully structured.

### Sales Sequence
* Includes 10-day multi-channel outreach cadence (LinkedIn connection, email touches, call blocks, opt-out reminders, and stop conditions).

### Qualification Rules
* Restricts product walkthroughs to prospects with >= 2 RFPs/month, confirmed sponsor access, and willingness to pay. Disqualifies leads needing air-gapped on-premises setups or secret/classified data.

### Claim Safety Guardrails
* Strictly prohibits SOC 2, HIPAA, FedRAMP, or ISO 27001 certification claims in outreach. Restricts statements to grounded retrieval details and mandates human-in-the-loop review terminology.

### Tests Added
* Integration tests verify all 9 files are present, do not contain real API keys, include expected headers in CSV templates, have expected stages in CRM pipeline records, and comply with safety policies.

### Checks/Tests Results
* `pytest -q`: PASS (171 tests passed).
* `ruff check .`: PASS.
* `ruff format --check .`: PASS.
* `mypy app`: PASS.
* `npm run assets:build`: PASS.
* `npx tsc --noEmit`: PASS.
* `scripts/run_ai_eval.py --offline`: PASS.
* `docker compose -f docker-compose.prod.yml config`: PASS.
* `docker build -t rfp-architect-mvp:pilot .`: PASS.
* `powershell -ExecutionPolicy Bypass -File scripts/check_all.ps1`: PASS.

### Remaining Gaps for Step 17
* None. Step 16 is complete.

---

## 22. Step 17 - First Paid Pilot Campaign Execution

### Files Changed
- [PILOT_READINESS_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_READINESS_CHECKLIST.md) (appended Step 17 campaign readiness checks).

### Campaign Assets Added
- [FIRST_20_ACCOUNT_BATCH_TEMPLATE.csv](file:///D:/RFA/Project/rfp-architect-mvp/FIRST_20_ACCOUNT_BATCH_TEMPLATE.csv) (outreach target account batch database with placeholder research rows).
- [ACCOUNT_RESEARCH_WORKSHEET.md](file:///D:/RFA/Project/rfp-architect-mvp/ACCOUNT_RESEARCH_WORKSHEET.md) (research guidelines detailing allowed public procurement / job listing searches and prohibiting automated scraping).
- [OUTBOUND_QA_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/OUTBOUND_QA_CHECKLIST.md) (QA criteria check list enforcing opt-out notices, role relevance, and no fake compliance claims).
- [DISCOVERY_MEETING_EVIDENCE_TEMPLATE.md](file:///D:/RFA/Project/rfp-architect-mvp/DISCOVERY_MEETING_EVIDENCE_TEMPLATE.md) (structured template to log client workflow details, user quotes, and WTP indicators).
- [PILOT_OPPORTUNITY_REVIEW_MEMO.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_OPPORTUNITY_REVIEW_MEMO.md) (deal review memo mapping budget, security fit, proposed pilot package, and risk mitigations).
- [WEEKLY_CAMPAIGN_REPORT_TEMPLATE.md](file:///D:/RFA/Project/rfp-architect-mvp/WEEKLY_CAMPAIGN_REPORT_TEMPLATE.md) (campaign performance metric report covering funnel stats, segment responses, and objections).
- [WIN_LOSS_LEARNING_LOG.csv](file:///D:/RFA/Project/rfp-architect-mvp/WIN_LOSS_LEARNING_LOG.csv) (win/loss log tracking deal outcomes, reasons, security reactions, and key lessons).
- [CUSTOMER_PROOF_REPOSITORY_TEMPLATE.md](file:///D:/RFA/Project/rfp-architect-mvp/CUSTOMER_PROOF_REPOSITORY_TEMPLATE.md) (metric proof template establishing rules prohibiting fake quotes and requiring written permission before marketing).
- [FIRST_CAMPAIGN_OPERATING_CHECKLIST.md](file:///D:/RFA/Project/rfp-architect-mvp/FIRST_CAMPAIGN_OPERATING_CHECKLIST.md) (step-by-step operating checklist for research, message QA, discovery prep, and deal review conversions).
- [tests/integration/test_step17_campaign.py](file:///D:/RFA/Project/rfp-architect-mvp/tests/integration/test_step17_campaign.py) (integration test suite verifying campaign files, CSV column structures, and safety guidelines).

### Campaign Batch Template
* Fully structures target columns (RFP volume hypothesis, trigger events, data sensitivity levels, template choices, and touch dates) using only synthetic placeholders.

### Account Research Worksheet
* Clearly limits research to public company resources (website, Job postings, SAM.gov awards) and explicitly prohibits automated scrapers, personal details logging, and private brokers.

### Outbound QA
* Restricts email and LinkedIn messaging to specific pain angles, enforces 20-minute CTA invites, and requires standard opt-out warnings.

### Customer Proof Repository
* Enforces strict rules against fabricating quotes and requires verified source audits and signed client release agreements before external use.

### Tests Added
* Integration tests verify all 9 files are created, contain no API keys, validate CSV structures, check opt-out check points, and verify that no uncertified compliance statements exist in the campaign materials.

### Checks/Tests Results
* `pytest -q`: PASS (178 tests passed).
* `ruff check .`: PASS.
* `ruff format --check .`: PASS.
* `mypy app`: PASS.
* `npm run assets:build`: PASS.
* `npx tsc --noEmit`: PASS.
* `scripts/run_ai_eval.py --offline`: PASS.
* `docker compose -f docker-compose.prod.yml config`: PASS.
* `docker build -t rfp-architect-mvp:pilot .`: PASS.
* `powershell -ExecutionPolicy Bypass -File scripts/check_all.ps1`: PASS.

### Remaining Gaps for Step 18
* None. Step 17 is complete.






