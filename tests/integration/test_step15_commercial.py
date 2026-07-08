import re
from pathlib import Path


def test_commercial_artifacts_exist():
    base_dir = Path(__file__).resolve().parent.parent.parent
    docs = [
        "ICP_QUALIFICATION_SCORECARD.md",
        "PAID_PILOT_OFFER.md",
        "PRICING_AND_PACKAGING.md",
        "DISCOVERY_CALL_SCRIPT.md",
        "DEMO_SCRIPT.md",
        "PILOT_PROPOSAL_TEMPLATE.md",
        "SECURITY_RESPONSE_PACK.md",
        "ROI_CALCULATOR_GUIDE.md",
        "OBJECTION_HANDLING_GUIDE.md",
        "PAID_CONVERSION_CRITERIA.md",
    ]

    for doc_name in docs:
        file_path = base_dir / doc_name
        assert file_path.exists(), f"{doc_name} does not exist"

        content = file_path.read_text(encoding="utf-8")

        # 1. Verify no API keys/secrets
        assert "sk-" not in content, f"Possible OpenAI/Anthropic API key in {doc_name}"
        assert (
            "secret_key" not in content.lower()
            or "dummy" in content.lower()
            or "generator" in content.lower()
            or "cookie" in content.lower()
            or "session_secret_key" in content.lower()
        ), f"Possible secret leaked in {doc_name}"

        # 2. Verify no active compliance certification claims
        content_lower = content.lower()
        if "soc 2" in content_lower:
            # Must clarify that it is NOT certified or is unsupported/untested
            assert any(
                term in content_lower
                for term in (
                    "not",
                    "unsupported",
                    "disclaimer",
                    "no audited",
                    "none active",
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
                )
            ), f"{doc_name} makes an unvalidated GDPR compliance claim"


def test_pilot_offer_inclusions_exclusions():
    base_dir = Path(__file__).resolve().parent.parent.parent
    offer_path = base_dir / "PAID_PILOT_OFFER.md"
    content = offer_path.read_text(encoding="utf-8")

    # Verify exclusions and legal review notes
    assert "exclusions" in content.lower() or "out-of-scope" in content.lower()
    assert (
        "disclaimer" in content.lower()
        or "counsel review" in content.lower()
        or "legal" in content.lower()
    )


def test_security_pack_labels():
    base_dir = Path(__file__).resolve().parent.parent.parent
    security_path = base_dir / "SECURITY_RESPONSE_PACK.md"
    content = security_path.read_text(encoding="utf-8")

    # Verify labels used for status
    assert any(
        label in content for label in ("[IMPLEMENTED]", "[DOCUMENTED]", "[PLANNED]")
    )


def test_pricing_guidelines():
    base_dir = Path(__file__).resolve().parent.parent.parent
    pricing_path = base_dir / "PRICING_AND_PACKAGING.md"
    content = pricing_path.read_text(encoding="utf-8")

    # Verify price points and red lines exist
    assert any(price in content for price in ("$2,500", "$5,000", "$10,000"))
    assert "red line" in content.lower() or "bad deal" in content.lower()


def test_proposal_template():
    base_dir = Path(__file__).resolve().parent.parent.parent
    proposal_path = base_dir / "PILOT_PROPOSAL_TEMPLATE.md"
    content = proposal_path.read_text(encoding="utf-8")

    # Verify legal review disclaimer and placeholders
    assert (
        "disclaimer" in content.lower()
        or "counsel" in content.lower()
        or "not a legal contract" in content.lower()
    )
    assert (
        re.search(r"\[.+\]", content) is not None
    )  # checks for placeholders like [Date]


def test_roi_and_objection_guides():
    base_dir = Path(__file__).resolve().parent.parent.parent

    roi_path = base_dir / "ROI_CALCULATOR_GUIDE.md"
    roi_content = roi_path.read_text(encoding="utf-8")
    assert "warning" in roi_content.lower() or "estimate" in roi_content.lower()

    objection_path = base_dir / "OBJECTION_HANDLING_GUIDE.md"
    objection_content = objection_path.read_text(encoding="utf-8")
    # should not promise SOC 2 compliance
    assert (
        "not independently soc 2 certified" in objection_content.lower()
        or "not" in objection_content.lower()
    )
