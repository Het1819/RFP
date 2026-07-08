# RFP Architect - Paid Pilot Program Scope & Offer

This document defines the packages, inclusions, exclusions, and exit paths for the paid pilot program of RFP Architect.

---

> [!NOTE]
> **DISCLAIMER:** This is an operational program offer template. It does not constitute a legally binding agreement. All pilot terms and pricing must be reviewed and approved by company counsel and customer representatives before execution.

---

## 1. Pilot Package Options

We offer three standard pilot program structures:

| Parameter | Standard Pilot (Recommended) | Enterprise Pilot | Accelerated Pilot |
| :--- | :--- | :--- | :--- |
| **Duration** | **4 Weeks** | **6 Weeks** | **2 Weeks** |
| **Active Projects** | Up to 5 projects | Up to 15 projects | Up to 2 projects |
| **Seats/Users** | Up to 10 reviewers | Up to 30 reviewers | Up to 3 reviewers |
| **Mock RFPs** | 5 documents | Unlimited | 2 documents |
| **Support SLA** | Next-business-day | 4-hour business day | Email support |
| **Pricing Target** | $5,000 | $10,000 | $2,500 |

---

## 2. In-Scope Workflows

The pilot covers all core features of the RFP Architect MVP workspace:
1. **RFP Upload & Parsing:** Auto-extraction of requirements from PDF, DOCX, or TXT documents (up to 20MB per file).
2. **Knowledge Base Retrieval:** Indexing up to 50 company knowledge documents (such as capabilities statements or security sheets).
3. **Evidence Citation & Grounding:** Access to the grounding engine, retrieving page numbers and source snippets.
4. **Draft Response Generation:** Automatic drafts for requirements based strictly on retrieved source evidence.
5. **Human-in-the-Loop Review:** Status transitions (`NEEDS_REVIEW`, `APPROVED`, `NEEDS_EVIDENCE`) and routing tasks to reviewers.
6. **Exports:** Unlimited downloads of DOCX proposal drafts and XLSX compliance matrices.
7. **KPI Dashboard:** Access to the `/projects/ops/dashboard` metric console.

---

## 3. Out-of-Scope (Exclusions)

* **Production SLA:** The pilot is run on a staging environment and does not include commercial high-availability SLAs.
* **Sensitive Compliance Data:** No ITAR, FedRAMP High, or classified government data is permitted.
* **PCI/HIPAA Data:** Regulated cardholder data (PCI-DSS) or protected health information (PHI) is excluded unless custom contractual terms are separately approved.
* **Custom Integrations:** Direct CRM connection (Salesforce, HubSpot) or ticketing integrations (Jira) are excluded.
* **No Autonomous Submissions:** The software does not interact with government procurement portals or customer email hosts.

---

## 4. Responsibilities

### Customer Responsibilities:
* Provide non-confidential, mock RFP files for parsing.
* Allocate at least 1 Proposal Lead and 2 Technical SMEs for validation tests.
* Participate in weekly 30-minute sync calls.
* Complete the Exit Survey upon pilot completion.

### Vendor Responsibilities:
* Deploy a dedicated staging instance of RFP Architect.
* Conduct a 60-minute onboarding workshop.
* Resolve blocker bugs within target SLA windows.
* Provide an aggregated KPI report at pilot exit.

---

## 5. Exit Paths & Next Steps

At the end of the pilot period, the customer selects one of three exit options:
1. **Convert to Annual Subscription:** Move the staging tenant database to the production pipeline and transition to standard commercial pricing.
2. **Extend Pilot:** Purchase a 2-week extension (billed pro-rata) to complete validation of open requirements.
3. **Conclude Program:** Terminate the pilot instance and execute the data deletion runbook (database wipe and upload directory erasure).
