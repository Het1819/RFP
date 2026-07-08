# RFP Architect MVP - Pilot Participant Quickstart

This quickstart guide provides step-by-step instructions for pilot participants to execute core workflows.

---

## 1. Access & Sign In
1. Open your browser and navigate to the pilot deployment URL (e.g., `http://localhost:8000` or the staging server URL).
2. Enter your assigned pilot email address (e.g., `pilot@example.com`).
3. Click **Sign In**. (For OIDC SSO deployments, follow the on-screen enterprise single sign-on redirect).

## 2. Create a Proposal Project
1. From the dashboard page, click **Create New Project**.
2. Fill in the project details:
   - **Project Name:** (e.g., "Enterprise Cloud Infrastructure RFP")
   - **Client Name:** (e.g., "MegaCorp Enterprises")
   - **Due Date:** Select the deadline.
3. Click **Save Project**.

## 3. Upload RFP and Source Documents
1. Inside the project view, navigate to the **Documents** panel.
2. Click **Upload Document**.
3. Select your RFP document (PDF or DOCX, max 20MB). Set the document role to **RFP**.
4. Upload any supporting company knowledge files (such as product specs or security whitepapers) and set their role to **Supporting Evidence**.
5. Wait for the background worker to complete extraction. The document status will transition from `pending` to `succeeded`.

## 4. Review Extracted Requirements
1. Click the **Compliance Matrix** tab to see the requirements extracted from the RFP.
2. Verify that each row contains the correct requirement text, section header, and page number.
3. Check the assigned status: newly extracted items start in the `NEEDS_REVIEW` state.

## 5. Verify Evidence Grounding & AI Drafts
1. Select a requirement to open the details view.
2. Click **Retrieve Evidence** to run search queries against your supporting documents.
3. Review the retrieved snippets and verify that the page number match the source.
4. Click **Generate Draft Response**.
5. Inspect the generated response:
   - If the response is fully backed by retrieved source snippets, proceed to review.
   - If the system could not find evidence, verify that the status is set to `NEEDS_EVIDENCE`. Do not accept fabricated claims.

## 6. Edit & Approve Responses
1. To make corrections, edit the draft response content directly in the text editor.
2. Once satisfied that the answer is accurate and fully grounded, click **Approve Response**. This transitions the requirement status to `APPROVED`.
3. If additional clarification is needed from another user, type a note in the comments section.

## 7. Export Proposal & Submit Feedback
1. Once all mandatory requirements are approved, click the **Export** button in the project toolbar.
2. Select **Export DOCX** to get the proposal response draft or **Export XLSX** to retrieve the compliance matrix.
3. Report any bugs, latency issues, or usability suggestions by clicking **Submit Pilot Feedback** in the navigation bar or by visiting `/feedback` in your browser.
