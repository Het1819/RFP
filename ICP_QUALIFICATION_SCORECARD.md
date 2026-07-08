# RFP Architect - ICP & Buyer Qualification Scorecard

This qualification scorecard helps capture capture managers, sales leads, and operator personnel qualify prospects for the RFP Architect paid pilot program.

---

## 1. Ideal Customer Profile (ICP) Segmentations

* **Primary Target Profiles:**
  * **IT Services & Consulting Firms:** 50–1000 employees bidding on software development, infrastructure, or digital transformation contracts.
  * **Systems Integrators:** Providers deploying complex software suites (e.g. ERP, CRM, custom platforms) needing compliance trace matrices.
  * **Government Contractors:** Bid teams addressing federal/state solicitations that require rigid compliance cross-referencing and validation.
  * **Enterprise Proposal Teams:** Mid-market to enterprise companies processing at least 3 complex, multi-million-dollar RFPs per month.
  * **Cybersecurity Consulting Firms:** Audit and compliance consultancies responding to technical security questionnaires.

---

## 2. Key Buyer Roles & Personas

* **VP of Sales / Proposal Director (Economic Buyer):**
  * *Pain:* Bidding speed, proposal quality, resource burnout, and low win rates.
* **CTO / Head of Delivery (Sponsor/Influencer):**
  * *Pain:* SME time lost to drafting and compliance verification.
* **Capture Manager (Champion):**
  * *Pain:* Excel matrix manual creation, routing gaps to human reviewers, and cross-referencing documents.
* **Compliance / Security Lead (Gatekeeper):**
  * *Pain:* Data-handling policies, LLM transit leakage, and verifying security controls.

---

## 3. Disqualification & Red Flags

A prospect must be **disqualified** or flagged if:
1. **Low RFP Volume:** Processes less than 1 complex RFP per month (tool value-add does not justify cost).
2. **Classified Data Requirement:** Requires FedRAMP High, ITAR, or classified data hosting *within the pilot window* (not supported in MVP).
3. **No Support for AI Providers:** Strict security guidelines that block sending data to Anthropic/OpenAI APIs.
4. **Autonomous Expectations:** Expects 100% automated proposals without human-in-the-loop review.
5. **No SME Availability:** Unwilling to allocate at least 2 Subject Matter Experts to participate in review workflows during the pilot.

---

## 4. Scorecard Matrix

Rate each prospect from 1 to 5 on the following dimensions:

| Dimension | Evaluation Criteria | Score (1-5) |
| :--- | :--- | :--- |
| **RFP Volume** | 1: <1 RFP/month \| 3: 2-3 RFPs/month \| 5: >5 RFPs/month | |
| **SME Burnout** | 1: Low pain \| 3: SMEs complain about drafts \| 5: Severe bottleneck | |
| **Willingness-to-Pay**| 1: Bids are small \| 3: Has tool budget \| 5: High-value enterprise bids | |
| **AI Support** | 1: Strictly blocked \| 3: Restricted API review \| 5: Sandbox/enterprise LLM approved | |
| **Urgency** | 1: Just browsing \| 3: Bidding peak in Q3 \| 5: Active complex bid next month | |
| **Technical Stack** | 1: Legacy on-prem only \| 3: Standard Office/VPC \| 5: Cloud native (Word/Excel) | |

### Scoring Matrix Thresholds:
* **24–30 Points:** **GO (Strong Fit).** Proceed directly to paid pilot proposal.
* **18–23 Points:** **CONDITIONAL GO.** Address specific barriers (e.g. security approval, SME alignment) before proposal.
* **< 18 Points:** **NO-GO.** Do not proceed; customer is too early or out of scope.
