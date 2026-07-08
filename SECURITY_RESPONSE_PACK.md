# RFP Architect - Security Response Pack

This security questionnaire response pack provides clear, validated answers regarding the security architecture, data handling, and technical controls implemented in RFP Architect MVP.

---

## 1. Core Platform Technical Security Controls

### Q1.1: What authentication model is used to secure user sessions?
* **Answer:** RFP Architect enforces explicit cookie-based session authentication. User sessions are verified on every request using a signed cookie (`rfp_session`) containing encrypted JSON metadata. Unauthenticated browser GET requests are automatically redirected to `/login`, and API requests return 401.
* **Status:** **[IMPLEMENTED]**

### Q1.2: How is Cross-Site Request Forgery (CSRF) prevented?
* **Answer:** A dedicated CSRF middleware intercepts all state-changing HTTP requests (POST, PUT, PATCH, DELETE). It checks for a cryptographically secure token stored in the session cookie against the token provided in request form bodies or headers (`x-csrf-token`), rejecting mismatches with a 403 Forbidden.
* **Status:** **[IMPLEMENTED]**

### Q1.3: How is tenant data isolated across organizations?
* **Answer:** Multi-tenancy is enforced at the database layer. Every database table contains an `organization_id` column. API endpoints retrieve the authenticated organization ID from the secure session cookie and append it as a strict filter on all SQLAlchemy queries. Cross-tenant access attempts trigger security warnings and audit events.
* **Status:** **[IMPLEMENTED]**

### Q1.4: How are permissions and object-level access controlled?
* **Answer:** Object-level authorization checks verify that the requesting user's organization owns the resource (e.g. `project_id`, `requirement_id`) before returning or modifying the object. Attempts to access foreign records return 404.
* **Status:** **[IMPLEMENTED]**

---

## 2. Infrastructure & Operations

### Q2.1: How are backups managed and validated?
* **Answer:** Database backups are generated daily using `pg_dump` and stored in isolated volume mounts. Let's refer to our restore validation script (`scripts/restore.py`) which is run to practice disaster recovery.
* **Status:** **[IMPLEMENTED]**

### Q2.2: How are long-running processing jobs managed?
* **Answer:** File parsing and AI draft generation run asynchronously via a Redis-backed queue managed by `arq`. The worker isolates file execution, checks job health, and persists failures with detailed traceback logging.
* **Status:** **[IMPLEMENTED]**

### Q2.3: What security checks are run on software releases?
* **Answer:** Every commit must pass release gate checks in our CI pipeline: Ruff linting and formatting, Mypy type-checking, Trivy container vulnerability scanning, and Docker build compilation.
* **Status:** **[IMPLEMENTED]**

---

## 3. Artificial Intelligence & Data Flows

### Q3.1: Do you log LLM inputs and completions?
* **Answer:** Yes, LLM telemetry logs input/output token counts, model names, latency, and request status to structured logs for security auditing. Prompt text is not logged in production files.
* **Status:** **[IMPLEMENTED]**

### Q3.2: Is customer data used to train public LLM models?
* **Answer:** No. In production settings, we utilize enterprise LLM provider agreements with zero data-retention policies for training. Customer uploads are processed in transit only and are never fed back into public model weights.
* **Status:** **[DOCUMENTED / Requires Customer Review of LLM Keys]**

---

## 4. Compliance Audits & Known Limitations

### Q4.1: Is this environment SOC 2 certified or HIPAA compliant?
* **Answer:** No. RFP Architect MVP is not independently audited for SOC 2, HIPAA, GDPR, FedRAMP, or ISO 27001 certifications. While we incorporate best practices (session signatures, organization isolation, parameter validation) into our engineering design, the system has not been certified by an external auditor.
* **Status:** **[UNSUPPORTED CLAIM - NONE ACTIVE]**

### Q4.2: What are the current data limits during the pilot?
* **Answer:** The staging pilot limit is set to a maximum of 20MB per uploaded file, 5 active projects, and 50 knowledge documents per tenant database.
* **Status:** **[DOCUMENTED]**
