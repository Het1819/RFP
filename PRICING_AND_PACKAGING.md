# RFP Architect - Pricing & Packaging Recommendation

This document defines the packaging, pricing models, usage limits, and commercial policies for RFP Architect MVP.

---

## 1. Pilot Program Price Ranges

To drive qualification and prove customer commit, pilots are billed at a flat fee:
* **Accelerated Pilot (2 weeks):** **$2,500** flat fee. Up to 3 users, 2 active projects.
* **Standard Pilot (4 weeks):** **$5,000** flat fee. Up to 10 users, 5 active projects. (Recommended default package).
* **Enterprise Pilot (6 weeks):** **$10,000** flat fee. Up to 30 users, 15 active projects, premium support SLA.

---

## 2. Platform Packaging & Usage Metrics

Post-pilot commercialization uses a **hybrid subscription model**:
* **Base Platform Fee:** Billed annually. Grants access to organization workspaces, database persistence, and standard security updates.
* **Usage Allowances:** Bundled into base subscriptions, with overages billed on a monthly tier.
* **Usage Metric Options:**
  * **Option A: User Seats (Recommended for MVP):** Scale by the number of active reviewers (e.g. RFP managers + SMEs). Simplifies budget planning for buyers.
  * **Option B: Page Volume:** Scale by the number of pages processed (RFP documents + evidence files). Direct correlation to API execution costs.
  * **Option C: Proposal Exports:** Scale by the number of DOCX exports generated. Ties pricing directly to business outcomes.

---

## 3. Why Free Pilots are Risky (and When Acceptable)

### The Risk of Free Pilots:
* **No Stakeholder Skin-in-the-Game:** Free pilots are frequently abandoned due to competing priorities; paid pilots force management attention.
* **Security Obstacles:** Customers rarely prioritize legal/security questionnaire reviews for free tools.
* **Unqualified pipeline:** Attracts looky-loos who do not have high RFP volume or real budget.

### When Free is Acceptable:
* **Strategic Logo:** High-value enterprise account that can act as a pilot case study and reference.
* **Contractual Conversion Guarantee:** Customer signs a letter of intent (LOI) to convert to a paid contract if pre-defined success metrics are hit.

---

## 4. Post-Pilot Commercial Hypotheses

* **Standard SMB Tier:** **$1,200 / month** (billed annually). Includes 5 seats, up to 10 RFPs processed per month.
* **Mid-Market Tier:** **$3,500 / month** (billed annually). Includes 20 seats, up to 30 RFPs processed per month.
* **Enterprise Tier:** **$8,000+ / month** (billed annually). Dedicated database deployment, unlimited seats, custom page volume limits.

---

## 5. Margin & Cost Breakdown (Unit Economics)

To maintain healthy margins (> 80.0%), monitor the following estimated unit costs:
* **LLM Cost (Fake Provider during tests):** Live API cost ranges from $0.05 to $0.50 per RFP run (using Claude 3.5 Sonnet / GPT-4o) depending on document length.
* **Hosting Cost:** Dedicated small PostgreSQL + Redis instances in AWS/GCP run ~$150/month.
* **Support Cost:** Customer support and bug fixes run ~$50/user/month.
* **Margin Target:** Maintain base platform pricing to ensure gross margins exceed 80% on all tiers.

---

## 6. Discounting Policies & Red Lines

### Approved Discounts:
* Up to **15% off** for pilot conversion signed within 10 days of pilot exit.
* Up to **20% off** for multi-year commitments (2+ years).

### Commercial Red Lines (Bad Deals):
* **No Custom Code Modification:** Reject deals requiring proprietary custom feature development that deviates from the core SaaS roadmap.
* **No Fixed-Price On-Premises Maintenance:** Do not support customer-hosted deployments without a high-margin enterprise premium SLA.
* **No Liability for AI Content:** Never accept contract terms that hold the vendor liable for proposal content correctness (always enforce the customer's human-in-the-loop review responsibilities).
