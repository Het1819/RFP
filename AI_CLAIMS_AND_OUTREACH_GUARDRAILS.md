# AI Claims & Outreach Guardrails

This document establishes strict guidelines and guardrails for outreach claims, ensuring that all marketing, outbound communications, and sales discussions represent the technical capabilities of RFP Architect MVP accurately without making false compliance or performance statements.

---

## 1. Prohibited Claims (Hard Red Lines)

> [!CAUTION]
> The following claims are **strictly prohibited** in all outreach, emails, demos, and sales conversations:
> 1. **No Audited Compliance Certifications:** Do not claim that the software is SOC 2 certified, HIPAA compliant, GDPR certified, FedRAMP authorized, or ISO 27001 audited.
> 2. **No Guaranteed Win Rates/ROI:** Do not promise or guarantee specific win rate increases or dollar savings.
> 3. **No Perfect Accuracy Claims:** Do not claim that the AI is error-free, never makes mistakes, or automatically drafts proposals without human review.
> 4. **No Autonomous Bid Submissions:** Do not claim that the software submits bids to procurement portals or communicates directly with purchasers.

---

## 2. Allowed Claims (Technical Truths)

You are **permitted** to make the following statements, as they are fully implemented and verified in our MVP checks:
* **Grounded Evidence Retrieval:** The AI drafts responses using strictly retrieved snippets from your uploaded documents, referencing the exact source page numbers.
* **Excel Matrix Parser:** Automatically extracts compliance clauses from uploaded RFPs (PDF/DOCX) into an interactive table, replacing manual Excel copying.
* **Human-in-the-Loop Workflow:** All generated drafts require review, status updates, and human approval before export.
* **Multi-Tenancy Layer:** Data is isolated at the database query layer scoped strictly to the authenticated organization.

---

## 3. Safe vs Unsafe Phrasing Examples

| Unsafe Phrase (Prohibited) | Safe Phrase (Allowed) |
| :--- | :--- |
| *"Our AI is SOC 2 certified and GDPR compliant."* | *"We build session auth, CSRF validation, and tenant isolation into our SaaS engineering, but our staging pilot is not independently certified."* |
| *"The software automatically writes your proposal and guarantees a 40% win rate increase."* | *"Our pilot benchmarks show that using grounded AI drafts can reduce manual matrix extraction and first-draft SME writing time by up to 30-40%."* |
| *"Our AI never hallucinates or makes mistakes."* | *"RFP Architect uses strict evidence grounding. If a requirement lacks historic document support, the system flags it as `NEEDS_EVIDENCE` instead of fabricating claims."* |
| *"We will host your classified defense contract documents."* | *"Our staging pilot has a maximum file size of 20MB and is restricted to non-confidential mock RFP files."* |

---

## 4. Human-Review Language Guidelines

When discussing the role of AI in the proposal process, always emphasize human responsibility:
* *"RFP Architect is designed to accelerate first-draft generation and trace evidence. The user's Subject Matter Experts must review and approve all content prior to export."*
* *"The system serves as a co-pilot for proposal reviews. Final compliance verification remains the responsibility of your proposal team."*
