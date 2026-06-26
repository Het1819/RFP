

\# RFP Architect Premium UI Rules



\## Product



RFP Architect is a human-controlled proposal response workspace.



Core workflow:



1\. Upload RFP.

2\. Extract requirements into a compliance matrix.

3\. Upload approved knowledge documents.

4\. Retrieve source evidence.

5\. Draft source-backed answers.

6\. Route unresolved items to human reviewers.

7\. Export DOCX proposal drafts and XLSX compliance matrices.



\## Non-negotiable product principles



\* Evidence before drafting.

\* Never present unsupported AI answers as verified.

\* Human review remains visible and important.

\* Do not claim fake security certifications, customer logos, accuracy rates, or ROI results.

\* Do not change backend workflow behavior unless explicitly asked.

\* Preserve all existing FastAPI routes, forms, HTMX interactions, tests, and database behavior.

\* Do not replace Jinja2 + HTMX with React, Next.js, Vue, or a new frontend framework.

\* Do not use Tesla logos, text, imagery, designs, code, animations, or exact layouts.



\## Design direction



Create an original premium enterprise SaaS experience inspired only by broad principles:



\* Cinematic full-screen landing-page hero

\* Minimal visual clutter

\* Strong typography

\* Deep graphite or black background

\* Cool blue or teal accent color

\* Spacious layout

\* Smooth subtle motion

\* Clean rounded panels

\* Professional workspace UI

\* Accessible contrast and keyboard navigation

\* Responsive desktop, tablet, and mobile layouts



\## Required visual system



\* Use CSS variables for spacing, typography, colors, shadows, borders, radius, and animation durations.

\* Prefer modern CSS grid, flexbox, clamp(), and container queries where practical.

\* Use Inter, Geist, Manrope, or Plus Jakarta Sans.

\* Avoid excessive gradients, glassmorphism, neon effects, emojis, and decorative clutter.

\* Use CSS or SVG illustrations created in this project. Do not use copyrighted images.

\* Use icons only from a permissive library already installed or create simple inline SVG icons.



\## Required pages



1\. Marketing home page

2\. Projects dashboard

3\. Project workspace

4\. Compliance matrix

5\. Requirement detail and review state

6\. Empty states

7\. Loading states

8\. Error states

9\. Mobile responsive layout



\## Landing-page content



Headline:

Turn complex RFPs into source-backed proposal drafts.



Subheadline:

Extract every requirement, reuse approved knowledge, validate evidence, and keep experts in control before submission.



Primary CTA:

Explore the workspace



Secondary CTA:

See how it works



Feature pillars:



\* Capture every requirement

\* Draft only with evidence

\* Keep experts in control

\* Export review-ready proposals



\## Engineering rules



\* Work in small vertical slices.

\* Before coding, inspect relevant templates, CSS, routes, and tests.

\* Show an implementation plan before making broad changes.

\* Keep all changes scoped to frontend unless backend changes are required for rendering.

\* Do not alter database schemas.

\* Add or update tests where appropriate.

\* Run:

&#x20; uv run ruff check .

&#x20; uv run ruff format --check .

&#x20; uv run mypy app

&#x20; uv run pytest -q

\* Use browser checks at desktop, tablet, and mobile widths.

\* Provide a final summary listing changed files, test results, known limitations, and screenshots.



