---
name: quality-gate
description: Review the current RFP Architect MVP changes for correctness, security, and product-scope discipline.
---

Review the current git diff.

Check:
- Tenant and organization boundaries.
- Pydantic validation.
- File upload validation.
- Prompt injection protection.
- Evidence citation enforcement.
- Missing tests.
- Unsafe defaults.
- Scope creep.
- Broken migrations.
- Export and parsing failure handling.

Do not edit files unless explicitly asked.
Return findings grouped as Critical, Important, and Nice to have.
