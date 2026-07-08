from pathlib import Path


def test_step13_files_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent

    env_staging = base_dir / ".env.staging.example"
    smoke_ps = base_dir / "scripts" / "smoke_test.ps1"
    smoke_sh = base_dir / "scripts" / "smoke_test.sh"
    readiness_checklist = base_dir / "PILOT_READINESS_CHECKLIST.md"
    runbook = base_dir / "RUNBOOK.md"
    seed_script = base_dir / "scripts" / "seed_pilot_demo.py"

    assert env_staging.exists(), ".env.staging.example does not exist"
    assert smoke_ps.exists(), "smoke_test.ps1 does not exist"
    assert smoke_sh.exists(), "smoke_test.sh does not exist"
    assert readiness_checklist.exists(), "PILOT_READINESS_CHECKLIST.md does not exist"
    assert runbook.exists(), "RUNBOOK.md does not exist"
    assert seed_script.exists(), "seed_pilot_demo.py does not exist"


def test_env_staging_contains_no_secrets():
    base_dir = Path(__file__).resolve().parent.parent.parent
    env_staging = base_dir / ".env.staging.example"
    content = env_staging.read_text(encoding="utf-8")

    assert "sk-" not in content, "Possible API key in .env.staging.example"
    assert "session_secret_key=replace-with" in content.lower(), (
        "Placeholder SESSION_SECRET_KEY not found or too specific"
    )
    assert "app_secret_key=replace-with" in content.lower(), (
        "Placeholder APP_SECRET_KEY not found or too specific"
    )


def test_seed_script_refuses_production():
    # Verify that seed_pilot_demo.py contains safety check
    base_dir = Path(__file__).resolve().parent.parent.parent
    seed_script = base_dir / "scripts" / "seed_pilot_demo.py"
    content = seed_script.read_text(encoding="utf-8")

    assert 'settings.APP_ENV == "production"' in content, (
        "No production safety check found in seed_pilot_demo.py"
    )
    assert "sys.exit(1)" in content, (
        "No sys.exit(1) on safety failure in seed_pilot_demo.py"
    )
