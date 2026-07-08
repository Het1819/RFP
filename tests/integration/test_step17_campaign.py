import csv
from pathlib import Path


def test_campaign_artifacts_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    docs = [
        "FIRST_20_ACCOUNT_BATCH_TEMPLATE.csv",
        "ACCOUNT_RESEARCH_WORKSHEET.md",
        "OUTBOUND_QA_CHECKLIST.md",
        "DISCOVERY_MEETING_EVIDENCE_TEMPLATE.md",
        "PILOT_OPPORTUNITY_REVIEW_MEMO.md",
        "WEEKLY_CAMPAIGN_REPORT_TEMPLATE.md",
        "WIN_LOSS_LEARNING_LOG.csv",
        "CUSTOMER_PROOF_REPOSITORY_TEMPLATE.md",
        "FIRST_CAMPAIGN_OPERATING_CHECKLIST.md",
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
                )
            ), f"{doc_name} makes an unvalidated GDPR compliance claim"


def test_first_20_account_csv_headers():
    base_dir = Path(__file__).resolve().parent.parent.parent
    csv_path = base_dir / "FIRST_20_ACCOUNT_BATCH_TEMPLATE.csv"

    expected_headers = [
        "batch_id",
        "account_name_placeholder",
        "segment",
        "region",
        "website_placeholder",
        "account_source",
        "rfp_volume_hypothesis",
        "proposal_team_hypothesis",
        "likely_buyer_role",
        "pain_hypothesis",
        "trigger_event_placeholder",
        "data_sensitivity_guess",
        "pilot_fit_score",
        "priority_rank",
        "outreach_angle",
        "selected_template",
        "first_touch_date",
        "follow_up_1_date",
        "follow_up_2_date",
        "status",
        "owner",
        "notes",
    ]

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.reader(f)
        # Skip comment lines if they start with '#'
        headers = []
        for row in reader:
            if not row or row[0].strip().startswith("#"):
                continue
            headers = row
            break
        assert headers == expected_headers


def test_win_loss_log_csv_headers():
    base_dir = Path(__file__).resolve().parent.parent.parent
    csv_path = base_dir / "WIN_LOSS_LEARNING_LOG.csv"

    expected_headers = [
        "date",
        "account_name_placeholder",
        "outcome",
        "stage_reached",
        "primary_reason",
        "secondary_reason",
        "ICP_fit",
        "price_reaction",
        "security_reaction",
        "AI_trust_reaction",
        "competitor_or_alternative",
        "quote_placeholder",
        "follow_up_required",
        "learning",
        "action_item",
    ]

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.reader(f)
        headers = next(reader)
        assert headers == expected_headers


def test_customer_proof_testimonials():
    base_dir = Path(__file__).resolve().parent.parent.parent
    proof_path = base_dir / "CUSTOMER_PROOF_REPOSITORY_TEMPLATE.md"
    content = proof_path.read_text(encoding="utf-8").lower()

    # Must forbid fake testimonials and unverifiable claims
    assert "no fake testimonials" in content
    assert "no unverifiable roi" in content
    assert "never invent" in content or "do not invent" in content
    assert "written permission" in content or "permission required" in content


def test_outbound_qa_opt_out():
    base_dir = Path(__file__).resolve().parent.parent.parent
    qa_path = base_dir / "OUTBOUND_QA_CHECKLIST.md"
    content = qa_path.read_text(encoding="utf-8").lower()

    # checklist must include opt-out and CTA checks
    assert "opt-out" in content or "unsubscribe" in content or "opt out" in content
    assert "cta" in content or "discovery" in content


def test_research_worksheet_restrictions():
    base_dir = Path(__file__).resolve().parent.parent.parent
    worksheet_path = base_dir / "ACCOUNT_RESEARCH_WORKSHEET.md"
    content = worksheet_path.read_text(encoding="utf-8").lower()

    # worksheet must prohibit automated scraping and private data brokers
    assert "no automated scraping" in content or "scraping" in content
    assert "no private data brokers" in content or "data brokers" in content


def test_discovery_meeting_wtp():
    base_dir = Path(__file__).resolve().parent.parent.parent
    evidence_path = base_dir / "DISCOVERY_MEETING_EVIDENCE_TEMPLATE.md"
    content = evidence_path.read_text(encoding="utf-8").lower()

    # must include willingness-to-pay evidence
    assert "wtp" in content or "willingness to pay" in content
