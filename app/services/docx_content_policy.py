"""Standalone bounded DOCX content-policy inspector.

This module performs a deeper, independent check on a file that has
already been recognized as a DOCX *candidate* by
``app.services.document_type_detection.detect_docx_candidate`` (A5b). It
looks for macros, embedded OLE objects, external relationships,
zip-bomb-style resource limits, path traversal, and malformed ZIP/XML
structure.

It is deliberately independently correct: it is safe to call this
function even if A5b's detector has not run first (e.g. from a future
orchestration step, or a test), so a handful of structural checks that
A5b's detector already performs (duplicate member names, member-count
cap) are re-checked here as defense in depth rather than assumed.

Every check reads only ZIP central-directory metadata
(``infolist()``/``getinfo()``) or a small bounded prefix of a member's
decompressed bytes. This module never calls ``ZipFile.extract`` /
``ZipFile.extractall`` and never writes a member to disk.

Only the Python standard library may be imported here, plus the A5b
detection module (for its hardened XML parser and macro content-type
marker, reused rather than duplicated).
"""

import re
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings
from app.services.document_type_detection import (
    _MACRO_CONTENT_TYPE_MARKER,
    _hardened_parse,
)

# Bump whenever the set of checks below changes, so callers/audit logs can
# tell which policy version produced a given result.
POLICY_VERSION = "1"

_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EMBEDDINGS_PREFIX = "word/embeddings/"
_VBA_PROJECT_MEMBER_NAME = "vbaProject.bin"
_CONTENT_TYPES_PART = "[Content_Types].xml"
_DOCUMENT_RELS_PART = "word/_rels/document.xml.rels"

# Generous but bounded cap on the identity/relationship XML parts this
# module reads in full into memory. Matches A5b's identity-part bound.
_MAX_INSPECTED_XML_BYTES = 1024 * 1024  # 1 MiB

# No legitimate OOXML part name ends in any of these -- any member that
# does is either a nested archive smuggled into the package or an attempt
# to confuse downstream tooling that infers behavior from extensions.
_NESTED_ARCHIVE_EXTENSIONS = (
    ".zip",
    ".docx",
    ".docm",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
)

# Windows drive-letter absolute path (``C:\...`` / ``C:/...``) or UNC path
# (``\\server\...``) used as a ZIP member name.
_DRIVE_OR_UNC_RE = re.compile(r"^[A-Za-z]:[\\/]|^\\\\")

_EXTERNAL_RELATIONSHIP_RE = re.compile(
    rb'<Relationship\b[^>]*\bTargetMode\s*=\s*"External"', re.IGNORECASE
)


@dataclass(frozen=True)
class DocxPolicyResult:
    passed: bool
    reason_code: str | None
    # DOCX_MACRO_PRESENT, DOCX_OLE_PRESENT, DOCX_EXTERNAL_RELATIONSHIP,
    # DOCX_ARCHIVE_LIMIT, DOCX_MALFORMED_PACKAGE
    policy_version: str


def _rejected(reason_code: str) -> DocxPolicyResult:
    return DocxPolicyResult(
        passed=False, reason_code=reason_code, policy_version=POLICY_VERSION
    )


def _read_member_bounded(zf: zipfile.ZipFile, name: str, max_bytes: int) -> bytes:
    """Read a single named zip member's bytes, bounded by declared size.

    Uses ``ZipFile.read`` only -- never ``extract``/``extractall`` -- and
    only after checking the declared (central-directory) size against
    ``max_bytes``, so no arbitrarily large member is ever decompressed
    into memory here.
    """
    info = zf.getinfo(name)
    if info.file_size > max_bytes:
        raise ValueError("member too large for policy inspection")
    return zf.read(name)


def _is_traversal_or_absolute(name: str) -> bool:
    if name.startswith("/") or name.startswith("\\"):
        return True
    normalized = name.replace("\\", "/")
    if ".." in normalized.split("/"):
        return True
    return bool(_DRIVE_OR_UNC_RE.match(name))


def check_docx_content_policy(file_path: Path) -> DocxPolicyResult:
    """Run the DOCX content-policy checks against ``file_path``.

    Assumes ``file_path`` refers to a file already believed to be a DOCX
    (e.g. having passed ``detect_docx_candidate``), but performs its own
    independent structural validation and never trusts the caller.
    """
    try:
        with zipfile.ZipFile(file_path) as zf:
            try:
                infolist = zf.infolist()
            except (zipfile.BadZipFile, struct.error, OSError):
                return _rejected("DOCX_MALFORMED_PACKAGE")

            # Defense in depth: A5b's detect_docx_candidate already caps
            # member count and rejects duplicate names, but this module
            # must be independently correct even if called first.
            if len(infolist) > settings.DOCX_DETECTION_MAX_MEMBERS:
                return _rejected("DOCX_ARCHIVE_LIMIT")

            names = [info.filename for info in infolist]
            if len(names) != len(set(names)):
                return _rejected("DOCX_MALFORMED_PACKAGE")

            total_uncompressed = 0
            total_compressed = 0
            for info in infolist:
                # Bit 0 of the general-purpose flag field marks the entry
                # as encrypted. A genuine OOXML package never encrypts
                # individual ZIP entries, so this is a strong forgery
                # signal, not a legitimate-but-unusual case.
                if info.flag_bits & 0x1:
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                if _is_traversal_or_absolute(info.filename):
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                if info.filename.lower().endswith(_NESTED_ARCHIVE_EXTENSIONS):
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                # Central-directory metadata only -- no decompression.
                total_uncompressed += info.file_size
                total_compressed += info.compress_size

            if total_uncompressed > settings.DOCX_MAX_UNCOMPRESSED_TOTAL_BYTES:
                return _rejected("DOCX_ARCHIVE_LIMIT")

            if total_compressed > 0:
                ratio = total_uncompressed / total_compressed
                if ratio > settings.DOCX_MAX_COMPRESSION_RATIO:
                    return _rejected("DOCX_ARCHIVE_LIMIT")
            elif total_uncompressed > 0:
                # Nonzero declared uncompressed size with zero compressed
                # bytes across the whole package is itself an anomaly
                # consistent with a forged zip-bomb-style entry.
                return _rejected("DOCX_ARCHIVE_LIMIT")

            name_set = set(names)

            if _VBA_PROJECT_MEMBER_NAME in {Path(n).name for n in names}:
                return _rejected("DOCX_MACRO_PRESENT")

            if _CONTENT_TYPES_PART in name_set:
                try:
                    content_types_bytes = _read_member_bounded(
                        zf, _CONTENT_TYPES_PART, _MAX_INSPECTED_XML_BYTES
                    )
                except (ValueError, KeyError, zipfile.BadZipFile):
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                try:
                    _hardened_parse(content_types_bytes)
                except ValueError:
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                content_types_text = content_types_bytes.decode("utf-8", "replace")
                if _MACRO_CONTENT_TYPE_MARKER in content_types_text:
                    return _rejected("DOCX_MACRO_PRESENT")

            for name in names:
                is_embedding = (
                    name.startswith(_EMBEDDINGS_PREFIX) and name != _EMBEDDINGS_PREFIX
                )
                if not is_embedding:
                    continue
                info = zf.getinfo(name)
                if info.is_dir():
                    continue
                try:
                    with zf.open(name) as member:
                        header = member.read(len(_OLE_MAGIC))
                except (zipfile.BadZipFile, OSError):
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                if header == _OLE_MAGIC:
                    return _rejected("DOCX_OLE_PRESENT")

            if _DOCUMENT_RELS_PART in name_set:
                try:
                    rels_bytes = _read_member_bounded(
                        zf, _DOCUMENT_RELS_PART, _MAX_INSPECTED_XML_BYTES
                    )
                except (ValueError, KeyError, zipfile.BadZipFile):
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                try:
                    _hardened_parse(rels_bytes)
                except ValueError:
                    return _rejected("DOCX_MALFORMED_PACKAGE")
                if _EXTERNAL_RELATIONSHIP_RE.search(rels_bytes):
                    return _rejected("DOCX_EXTERNAL_RELATIONSHIP")

    except (zipfile.BadZipFile, OSError):
        # OSError also covers a missing/unreadable file path: this
        # function must fail closed (rejected, not an unhandled
        # exception) for any input that cannot be opened as a ZIP.
        return _rejected("DOCX_MALFORMED_PACKAGE")

    return DocxPolicyResult(
        passed=True, reason_code=None, policy_version=POLICY_VERSION
    )
