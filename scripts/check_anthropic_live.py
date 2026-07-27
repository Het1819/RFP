# scripts/check_anthropic_live.py
# Optional, separately authorized operator command that makes ONE real
# request to the Anthropic API to confirm the configured key/model work.
#
# This is NOT run in CI and NOT part of normal startup -- it exists purely
# for a human operator to manually verify connectivity before a pilot.
#
# Usage:
#   uv run python scripts/check_anthropic_live.py --live
#
# Requires --live explicitly; running it with no arguments prints the
# warning below and exits without making any request. Sends a minimal
# fixed prompt only -- never customer, RFP, or knowledge-base data. Never
# prints the API key.

import argparse
import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_FIXED_PROMPT = "Reply with exactly one word: OK"


async def _run_live_check() -> int:
    from app.core.config import settings

    if settings.LLM_PROVIDER != "anthropic":
        print(f"ERROR: LLM_PROVIDER is {settings.LLM_PROVIDER!r}, not 'anthropic'.")
        return 1
    if not settings.ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY is not configured.")
        return 1

    model = settings.LLM_MODEL or "claude-sonnet-4-6"
    print(f"Making one live request to Anthropic (model={model})...")
    print("This incurs a real API call and possible cost.")

    from anthropic import AsyncAnthropic

    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        response = await client.messages.create(
            max_tokens=16,
            model=model,
            messages=[{"role": "user", "content": _FIXED_PROMPT}],
        )
    except Exception as exc:
        # Never print exc's str() verbatim if it might embed request
        # headers/auth material; report only the exception type.
        print(f"ERROR: live request failed ({type(exc).__name__}).")
        return 1

    text = getattr(response.content[0], "text", "") if response.content else ""
    print(f"Response received ({len(text)} chars). Connectivity confirmed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Make one real, minimal, fixed-prompt request to the Anthropic "
            "API to confirm the configured key/model are working. Not run "
            "in CI or at normal startup."
        )
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Required to actually make the request. Incurs cost.",
    )
    args = parser.parse_args()

    if not args.live:
        print(
            "This command makes a REAL request to the Anthropic API and "
            "may incur cost. Re-run with --live to proceed.\n"
            "No customer, RFP, or knowledge-base data is ever sent -- only "
            "a fixed, minimal prompt."
        )
        return 1

    return asyncio.run(_run_live_check())


if __name__ == "__main__":
    sys.exit(main())
