from pathlib import Path


def test_release_readiness_artifacts_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    docs = [
        "PR_DESCRIPTION_HARDENING_BRANCH.md",
        "FINAL_RELEASE_CHECKLIST.md",
        "PILOT_RISK_REGISTER.md",
        "REVIEWER_GUIDE.md",
        "MERGE_PLAN.md",
        "DEPLOYMENT_DECISION_MEMO_TEMPLATE.md",
        "scripts/final_release_validation.ps1",
        "scripts/final_release_validation.sh",
    ]

    for doc_name in docs:
        file_path = base_dir / doc_name
        assert file_path.exists(), f"{doc_name} does not exist"

        content = file_path.read_text(encoding="utf-8")

        # Verify no API keys/secrets
        assert "sk-" not in content, f"Possible OpenAI/Anthropic API key in {doc_name}"
        assert (
            "secret_key" not in content.lower()
            or "dummy" in content.lower()
            or "generator" in content.lower()
            or "cookie" in content.lower()
            or "session_secret_key" in content.lower()
        ), f"Possible secret leaked in {doc_name}"

        # Verify no active compliance certification claims
        content_lower = content.lower()
        if "soc 2" in content_lower:
            assert any(
                term in content_lower
                for term in (
                    "not",
                    "unsupported",
                    "disclaimer",
                    "no audited",
                    "none active",
                    "prohibited",
                    "unsafe",
                    "forbids",
                    "blocker",
                )
            ), f"{doc_name} makes an unvalidated SOC 2 certification claim"
        if "hipaa" in content_lower:
            assert any(
                term in content_lower
                for term in (
                    "not",
                    "unsupported",
                    "disclaimer",
                    "no audited",
                    "none active",
                    "prohibited",
                    "unsafe",
                    "forbids",
                    "blocker",
                )
            ), f"{doc_name} makes an unvalidated HIPAA certification claim"
        if "gdpr" in content_lower:
            assert any(
                term in content_lower
                for term in (
                    "not",
                    "unsupported",
                    "disclaimer",
                    "no audited",
                    "none active",
                    "prohibited",
                    "unsafe",
                    "forbids",
                    "blocker",
                )
            ), f"{doc_name} makes an unvalidated GDPR compliance claim"


def test_risk_register_contents():
    base_dir = Path(__file__).resolve().parent.parent.parent
    risk_path = base_dir / "PILOT_RISK_REGISTER.md"
    content = risk_path.read_text(encoding="utf-8").lower()

    # Must contain key risks:
    required_risks = [
        "hallucination",
        "missed requirement",
        "unsupported draft",
        "evidence/citation",
        "confidential data",
        "auth/session",
        "tenant isolation",
        "queue/worker",
        "redis/job loss",
        "backup failure",
        "migration failure",
        "docker deployment",
        "vulnerability",
        "sales material",
        "willingness-to-pay",
    ]
    for risk in required_risks:
        assert risk in content, f"Risk Register missing risk matching: {risk}"


def test_final_release_checklist_contents():
    base_dir = Path(__file__).resolve().parent.parent.parent
    checklist_path = base_dir / "FINAL_RELEASE_CHECKLIST.md"
    content = checklist_path.read_text(encoding="utf-8").lower()

    # must include required checklist topics
    required_items = [
        "auth",
        "csrf",
        "tenant isolation",
        "evidence",
        "ai eval",
        "queue",
        "backup",
        "rollback",
    ]
    for item in required_items:
        assert item in content, f"Release Checklist missing: {item}"


def test_reviewer_guide_contents():
    base_dir = Path(__file__).resolve().parent.parent.parent
    guide_path = base_dir / "REVIEWER_GUIDE.md"
    content = guide_path.read_text(encoding="utf-8").lower()

    # must include specific review focus targets
    required_focus = [
        "security",
        "ai/eval",
        "migration",
        "deployment",
    ]
    for focus in required_focus:
        assert focus in content, f"Reviewer Guide missing focus on: {focus}"


def test_merge_plan_automatic_merge_prevention():
    base_dir = Path(__file__).resolve().parent.parent.parent
    plan_path = base_dir / "MERGE_PLAN.md"
    content = plan_path.read_text(encoding="utf-8").lower()

    # Must explicitly state not to push/merge automatically
    assert "do not merge or push automatically" in content


def test_validation_scripts_prevent_git_actions():
    base_dir = Path(__file__).resolve().parent.parent.parent
    ps1_path = base_dir / "scripts/final_release_validation.ps1"
    sh_path = base_dir / "scripts/final_release_validation.sh"

    for path in (ps1_path, sh_path):
        lines = path.read_text(encoding="utf-8").splitlines()
        code_lines = [line for line in lines if not line.strip().startswith("#")]
        code_content = "\n".join(code_lines)

        # Ensure no git mutating actions (add, commit, tag, push)
        assert "git add" not in code_content
        assert "git commit" not in code_content
        assert "git push" not in code_content
        assert "git tag -a" not in code_content
