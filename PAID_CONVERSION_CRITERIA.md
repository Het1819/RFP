# RFP Architect - Paid Conversion Criteria & Checklist

This document details the transition thresholds, sign-off checklists, and required next steps to convert a staging pilot customer into a paid commercial subscriber.

---

## 1. Pilot Performance Thresholds

To transition from the pilot stage to a paid subscription, the following operational thresholds must be verified:

| Metric | Pilot Target | Paid Conversion Target |
| :--- | :--- | :--- |
| **Extraction Recall** | >= 90% (golden test benchmark) | >= 95% (verified by human audit) |
| **Evidence Validation**| 100% of claims cite page numbers | 100% of claims cite page numbers |
| **SME Drafting Time** | >= 30% reduction vs baseline | >= 40% reduction vs baseline |
| **Workspace Activity** | 2 active users per week | Daily active users (DAU) |
| **System Reliability** | Zero blocker bugs unresolved | < 2% error rate on queue processing |

---

## 2. Expansion & Stop Criteria

### Expansion Criteria (Up-sell Triggers):
* **Multi-Department Use:** Requesting seats for secondary teams (e.g. security engineering, legal reviewers).
* **Multi-Org Accounts:** Expanding workspace boundaries to support subsidiaries or partner organizations.
* **Volume Overage:** Exceeding the 5 active projects limit during the pilot.

### Stop Criteria (No-Go Triggers):
* **Security Obstruction:** Customer IT blocks access to the staging instance or rejects the enterprise zero-retention LLM api flow.
* **SME Abandonment:** SMEs do not log in or participate in review loops for more than 7 consecutive business days.
* **Hallucination Recurrence:** AI repeatedly generates claims that cannot be traced to uploaded source evidence.

---

## 3. Required Technical Fixes Before Commercial Rollout

Before transitioning a customer to the production environment:
1. **Clear Pilot Data:** Execute the database cleanup script to erase temporary mock RFP documents.
2. **Assign Production API Keys:** Replace demo LLM api credentials with customer-specific production keys.
3. **Configure Custom Domain:** Apply custom DNS routing (e.g. `rfp.customerdomain.com`) as required by the subscription contract.
4. **Final Security Questionnaire Sign-off:** Ensure the customer's risk team signs off on the final security response pack.

---

## 4. Buyer Sign-off Checklist

Before executing the annual SaaS agreement, the Capture Manager or VP of Sales must confirm:
* [ ] The 4-week pilot successfully demonstrated matrix setup time savings.
* [ ] The exported DOCX drafts match the formatting of the corporate template.
* [ ] The security questionnaire response pack is approved by IT compliance.
* [ ] Pricing terms (base platform fee + seat/volume tiers) are contractually aligned.
* [ ] The annual contract has undergone final legal review.
* [ ] Signature has been obtained on the proposal document.
