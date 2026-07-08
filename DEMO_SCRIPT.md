# RFP Architect - Demo Script & Guide

This script guides product demonstrators through 20-minute (express) and 45-minute (deep-dive) product demonstrations for prospects.

---

## 1. Demo Environment Pre-Flight Checklist

1. **Staging Environment Access:** Verify [http://localhost:8000](http://localhost:8000) (or staging URL) is active and running.
2. **Clear Prior Data:** Create a clean test project (e.g. "Acme Demo Project") beforehand.
3. **Seed Documents:** Pre-upload 3-5 knowledge documents (e.g. `security_policy_v2.pdf`, `capabilities_statement.pdf`) to ensure evidence is ready.
4. **Draft Files:** Have a clean sample RFP PDF ready for local upload (e.g. 5-page sample).
5. **Incognito Window:** Keep a separate incognito window logged in as a secondary SME reviewer to show routing.

---

## 2. Express Demo Flow (20 Minutes)

### Step 1: Project Creation & RFP Upload (3 mins)
* **Action:** Click "Create Project", name it, and upload the sample RFP file.
* **Talking Point:** *"Notice how quickly we ingest the RFP. The document is split and sent to the parsing parser, which extracts the text without executing scripts or macro payloads."*

### Step 2: Requirement Extraction & Compliance Matrix (5 mins)
* **Action:** Open the compliance matrix tab to show extracted rows.
* **Talking Point:** *"Rather than copy-pasting into Excel, RFP Architect extracts the distinct compliance clauses instantly. You have an interactive, editable table ready in seconds."*

### Step 3: Evidence Grounding & Draft Generation (7 mins)
* **Action:** Highlight a requirement, click "Generate Draft Response", and show the resulting draft with highlighted citations and page numbers.
* **Talking Point:** *"The AI doesn't write from memory. It searches our Postgres vector/full-text database, retrieves the exact page numbers from your uploaded security/capabilities files, and references them directly."*

### Step 4: Human Review & Export (5 mins)
* **Action:** Change status to `APPROVED` and click "Export to Word".
* **Talking Point:** *"Once your team reviews and approves, click Export to get a clean DOCX draft and XLSX compliance matrix matching your template."*

---

## 3. Deep-Dive Demo Flow (45 Minutes)

* **00:00–00:10:** Context setup and buyer paint-point alignment.
* **00:10–00:30:** Standard Express Demo flow.
* **00:30–00:40:** Deep-dive features:
  * **SME Routing:** Show how to assign a requirement to an SME. Log in as the SME, show the filtered task view, submit feedback, and approve.
  * **KPI Dashboard:** Show `/projects/ops/dashboard` displaying accuracy, SLA times, and manual editing rates.
  * **Security & Backups:** Explain database schema migrations, daily backups, and tenant isolation model.
* **00:40–00:45:** Next steps and pilot proposal mapping.

---

## 4. Security Posture Summary

* **SaaS Tenant Isolation:** Each customer's data is isolated at the database schema query layer.
* **Data Processing Integrity:** Files are stored in secure volume mounts; database tables are backed up daily.
* **CSRF & Auth Hardening:** Active session cookie verification and CSRF token validation are enforced on all mutating requests.

---

## 5. Critical Demo Boundaries: What NOT to Claim

> [!CAUTION]
> * **DO NOT CLAIM** that the software is SOC 2 certified or HIPAA compliant. State that we follow these security frameworks in our design but are not independently audited in MVP.
> * **DO NOT CLAIM** that the AI is 100% accurate. Emphasize that human-in-the-loop review is mandatory.
> * **DO NOT CLAIM** support for air-gapped/on-premises installations unless custom contract pricing is established.
