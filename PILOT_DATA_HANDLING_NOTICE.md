# RFP Architect MVP - Pilot Customer Data Handling Notice

This notice outlines data security, handling practices, and privacy boundaries for the RFP Architect MVP controlled pilot.

---

> [!IMPORTANT]
> **LEGAL NOTICE:** This document is an operational policy template. It must be formally reviewed and approved by your organization's legal, security, and compliance teams before deploying this application to external commercial customers.

---

## 1. Scope of Uploaded Data

### What You May Upload
During the controlled pilot phase, users are authorized to upload:
- Standard RFP solicitations (PDF, DOCX, TXT) that do not contain classified, proprietary, or contractually restricted information.
- Publicly available company whitepapers, product documentation, security questionnaires, and sales brochures to serve as supporting evidence.

### What You Must NOT Upload
Unless explicitly approved in writing under a custom pilot agreement, users **must not** upload:
- Highly confidential commercial secrets, pre-release intellectual property, or pricing strategies.
- Protected Health Information (PHI) subject to HIPAA regulations.
- Personally Identifiable Information (PII) of customers or employees (other than basic pilot user emails and full names required for logging in).
- Classified government records or ITAR-regulated technical data.

---

## 2. AI Provider Data Flow & Processing
* **Model Routing:** Extracted requirement text and retrieved evidence snippets are sent to the configured LLM Provider (Anthropic Claude API or OpenAI API) for processing.
* **No Model Training:** Under our provider enterprise agreements, data transmitted via APIs is processed solely to generate completions and is **not** used to train public models.
* **Data Transit:** All data in transit is encrypted using TLS 1.3.

---

## 3. Data Retention & Deletion Requests
* **Data Retention:** Uploaded documents and generated draft answers remain stored on the pilot server's persistent volumes for the duration of the pilot contract.
* **Deletion Process:** To request complete deletion of your organization's database records and uploaded files, contact the pilot administrator at `admin-pilot@yourorg.com`. Deletion requests are processed and completed within 5 business days.

---

## 4. Access Control & Organization Isolation
* **Access Scoping:** The application enforces strict organization-level isolation. Users can only view, edit, or export projects and documents that belong to their own authenticated organization.
* **Authentication:** All web requests require active session cookies. Direct unauthenticated attempts to access API routes or document paths return `401 Unauthorized` or redirect to the login screen.

---

## 5. Auditing, Observability, and Backups
* **Audit Logs:** Important user actions (e.g. `USER_LOGIN_SUCCESS`, `PROJECT_CREATE`, `EXPORT_DOCX`) are recorded in database audit events along with the user ID, timestamp, and request correlation ID.
* **Log Sanitization:** Sensitive input text, passwords, and API keys are automatically masked in the system logs to prevent accidental exposure in log files.
* **Database Backups:** Staging databases are backed up daily using PG dump tools. Backups are stored in encrypted host directories. Redis persistent append-only storage is enabled for worker task state, but it is not used for long-term database backups.

---

## 6. Incident Reporting
If you suspect a data breach, unauthorized access, or leak of pilot credentials:
1. Immediately contact the Incident Commander: Het Patel (`support-pilot@yourorg.com`).
2. Provide details of the affected user accounts, project IDs, or exposed log files.
3. The operations team will execute the rollback and isolation procedures outlined in the [RUNBOOK.md](file:///D:/RFA/Project/rfp-architect-mvp/RUNBOOK.md) to secure the staging environment.
