"""Text normalization utilities for parsed document output."""

import hashlib
import re
import unicodedata

# Keep tab, line feed, carriage return, and printable characters
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# Strip basic script / html tags if present in plain text output
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def normalize_text(text: str) -> str:
    """Normalize extracted text deterministically.

    1. NFKC Unicode normalization.
    2. Strip NUL bytes and control characters (preserving newline/tab).
    3. Strip HTML tag syntax.
    """
    if not text:
        return ""

    # NFKC normalization
    text = unicodedata.normalize("NFKC", text)

    # Replace NUL and unsafe control characters with space
    text = _CONTROL_CHAR_RE.sub("", text)

    # Strip HTML tags
    text = _HTML_TAG_RE.sub("", text)

    return text.strip()


def compute_sha256(text: str) -> str:
    """Compute SHA-256 digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
