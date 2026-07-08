import csv
from pathlib import Path


def test_sales_ops_artifacts_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    docs = [
        "TARGET_ACCOUNT_LIST_TEMPLATE.csv",
        "CRM_PIPELINE_TEMPLATE.csv",
        "OUTREACH_TEMPLATES.md",
        "SALES_SEQUENCE.md",
        "DISCOVERY_SCORECARD.md",
        "DEMO_QUALIFICATION_RULES.md",
        "PILOT_DEAL_REVIEW_CHECKLIST.md",
        "WEEKLY_SALES_CADENCE.md",
        "AI_CLAIMS_AND_OUTREACH_GUARDRAILS.md",
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
                )
            ), f"{doc_name} makes an unvalidated GDPR compliance claim"


def test_target_account_csv_headers():
    base_dir = Path(__file__).resolve().parent.parent.parent
    csv_path = base_dir / "TARGET_ACCOUNT_LIST_TEMPLATE.csv"

    expected_headers = [
        "account_name",
        "website",
        "segment",
        "country_region",
        "estimated_rfp_volume",
        "target_buyer_title",
        "buyer_department",
        "pain_hypothesis",
        "trigger_event",
        "data_sensitivity_level",
        "pilot_fit_score",
        "priority",
        "outreach_status",
        "next_action",
        "owner",
        "notes",
    ]

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        assert headers == expected_headers


def test_crm_pipeline_csv_headers_and_stages():
    base_dir = Path(__file__).resolve().parent.parent.parent
    csv_path = base_dir / "CRM_PIPELINE_TEMPLATE.csv"

    expected_headers = [
        "account_name",
        "contact_name_placeholder",
        "contact_role",
        "stage",
        "source",
        "last_touch_date",
        "next_touch_date",
        "pain_confirmed",
        "rfp_volume_confirmed",
        "security_blocker",
        "budget_signal",
        "decision_process",
        "pilot_price_discussed",
        "expected_pilot_value",
        "probability",
        "next_step",
        "close_plan",
        "notes",
    ]

    expected_stages = [
        "TARGET",
        "CONTACTED",
        "REPLIED",
        "DISCOVERY_BOOKED",
        "DISCOVERY_COMPLETED",
        "DEMO_BOOKED",
        "DEMO_COMPLETED",
        "SECURITY_REVIEW",
        "PILOT_PROPOSAL_SENT",
        "NEGOTIATION",
        "PAID_PILOT_WON",
        "CLOSED_LOST",
        "DISQUALIFIED",
    ]

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        assert headers == expected_headers

        stage_idx = headers.index("stage")
        for row in reader:
            if not row:
                continue
            stage_value = row[stage_idx]
            assert stage_value in expected_stages


def test_outreach_templates_and_guardrails():
    base_dir = Path(__file__).resolve().parent.parent.parent

    # Outreach templates must include opt-out warnings
    outreach_path = base_dir / "OUTREACH_TEMPLATES.md"
    content = outreach_path.read_text(encoding="utf-8").lower()
    assert "discovery call" in content or "discovery" in content
    assert (
        "error-free" not in content
        or "no claim" in content
        or "do not claim" in content
    )
    assert (
        "guaranteed" not in content
        or "no guaranteed" in content
        or "do not guarantee" in content
    )

    # Guardrails must have allowed/prohibited claims sections
    guardrails_path = base_dir / "AI_CLAIMS_AND_OUTREACH_GUARDRAILS.md"
    gr_content = guardrails_path.read_text(encoding="utf-8").lower()
    assert "prohibited claims" in gr_content
    assert "allowed claims" in gr_content
    assert "unsafe" in gr_content


def test_pilot_deal_review_checklist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    checklist_path = base_dir / "PILOT_DEAL_REVIEW_CHECKLIST.md"
    content = checklist_path.read_text(encoding="utf-8").lower()

    # checklist must include security, data, and legal review items
    assert "security" in content
    assert "data-handling" in content or "data deletion" in content
    assert "legal" in content
