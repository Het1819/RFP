import os
from pathlib import Path


def test_ci_workflow_files_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    ci_workflow_path = base_dir / ".github" / "workflows" / "ci.yml"
    release_workflow_path = base_dir / ".github" / "workflows" / "release.yml"

    assert ci_workflow_path.exists(), "ci.yml does not exist"
    assert release_workflow_path.exists(), "release.yml does not exist"


def test_ci_workflows_contain_no_secrets():
    base_dir = Path(__file__).resolve().parent.parent.parent
    workflows_dir = base_dir / ".github" / "workflows"

    for file_name in os.listdir(workflows_dir):
        if not file_name.endswith(".yml"):
            continue
        content = (workflows_dir / file_name).read_text(encoding="utf-8")

        # Basic protection checks
        assert "sk-" not in content, f"Possible Anthropic/OpenAI API key in {file_name}"
        assert "password" not in content.lower() or "secret" in content.lower(), (
            f"Check if {file_name} hardcodes plain passwords"
        )
        assert "enable_real_llm_eval" not in content.lower(), (
            f"{file_name} must not run real-provider evals"
        )


def test_local_check_scripts_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    ps_script = base_dir / "scripts" / "check_all.ps1"
    sh_script = base_dir / "scripts" / "check_all.sh"

    assert ps_script.exists(), "check_all.ps1 does not exist"
    assert sh_script.exists(), "check_all.sh does not exist"


def test_ci_documentation_exists():
    base_dir = Path(__file__).resolve().parent.parent.parent
    release_doc = base_dir / "RELEASE.md"
    deployment_doc = base_dir / "DEPLOYMENT.md"

    assert release_doc.exists(), "RELEASE.md does not exist"
    assert deployment_doc.exists(), "DEPLOYMENT.md does not exist"

    release_content = release_doc.read_text(encoding="utf-8")
    assert "branch protection" in release_content.lower(), (
        "RELEASE.md missing branch protection rules"
    )
    assert "rollback" in release_content.lower(), (
        "RELEASE.md missing rollback tag guidance"
    )
