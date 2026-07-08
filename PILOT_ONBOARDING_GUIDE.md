# RFP Architect MVP - Pilot Onboarding Guide

Welcome to the controlled customer pilot onboarding guide for the RFP Architect MVP workspace. This guide outlines the onboarding parameters, target users, system limits, and support channels to ensure a successful evaluation.

---

## 1. Pilot Purpose & Goals
The purpose of this pilot is to evaluate the usability, extraction quality, and drafting accuracy of the RFP Architect MVP in a controlled, live-production simulation. 

Our main goals are to:
- Validate that the compliance requirements extraction matches human expectations.
- Verify that AI-generated draft responses are backed by correct grounding evidence.
- Prove that users can easily audit and review requirements in the compliance matrix.
- Rehearse deployment health checks, rollback tags, and backup/restore cycles.

---

## 2. Target Users & Personas
* **RFP Manager / Bid Manager:** Primarily responsible for uploading the RFP document, setting up projects, assigning requirements, and exporting the final DOCX/XLSX.
* **Technical Reviewer / Subject Matter Expert (SME):** Responsible for evaluating evidence, approving draft answers, editing responses, and resolving compliance gaps.

---

## 3. What the Product Does (Core MVP Workflows)
1. **RFP Requirement Extraction:** Analyzes PDF/Word RFPs to extract mandatory/optional requirements.
2. **Knowledge Base Storage:** Indexes approved company knowledge documents to retrieve matching context.
3. **Evidence-backed Drafting:** Drafts proposals where every claim includes a direct source citation and page number.
4. **Human Review Workflow:** Routes review tasks and manages approval states (`APPROVED`, `NEEDS_REVIEW`, `NEEDS_EVIDENCE`).
5. **Multi-Format Export:** Outputs DOCX proposals and XLSX compliance matrices.

---

## 4. What the Product Does NOT Yet Do (Out of Scope for MVP)
* **No Autonomous Submissions:** The application will never submit bids automatically.
* **No Pricing Automation:** Bid pricing, financial estimators, and quotes are handled manually outside the application.
* **No External SaaS Integrations:** Integration with external CRMs (like Salesforce) or ticketing systems (like Jira) is deferred.

---

## 5. Supported Input Files & Size Expectations
* **File Types:** PDF (`.pdf`), Word (`.docx`), or Plain Text (`.txt`).
* **Maximum File Size:** 20MB per file.
* **Maximum Files per Project:** 5 documents (RFP and auxiliary files combined).

---

## 6. AI Limitations & Human-in-the-Loop Gate
* **No Hallucinations Accepted:** If the system cannot find valid evidence in the uploaded documents, it is instructed to return `NEEDS_EVIDENCE` rather than inventing statements.
* **Mandatory Human Approval:** No generated response can be exported or treated as final without being manually reviewed and marked as `APPROVED` by a pilot participant.

---

## 7. Data Handling Rules & Issue Reporting
* **Data Privacy:** Users must only upload non-sensitive, contractually approved test files during this phase. Refer to [PILOT_DATA_HANDLING_NOTICE.md](file:///D:/RFA/Project/rfp-architect-mvp/PILOT_DATA_HANDLING_NOTICE.md) for full guidelines.
* **Reporting Issues:** Interactive pilot participants can report bugs or issues directly within the application using the **Submit Pilot Feedback** page or by contacting the operator support channel.
* **Support Channel:** For high-priority support, email the pilot operations lead at `support-pilot@yourorg.com` or join the `#pilot-rfp-architect` Slack channel.

---

## 8. Suggested Pilot Schedule

| Phase | Activity | Duration | Participants |
| :--- | :--- | :--- | :--- |
| **Phase 1** | Onboarding & Staging Rehearsal | Days 1–2 | Operator / Admins |
| **Phase 2** | Mock RFP Upload & Requirement Audits | Days 3–5 | RFP Managers |
| **Phase 3** | SME Evidence Grounding & Draft Approvals | Days 6–10 | Technical SMEs |
| **Phase 4** | Export Verification & Exit Feedback | Days 11–12 | All Participants |
