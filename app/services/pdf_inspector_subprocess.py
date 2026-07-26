"""Isolated PDF structural/security-metadata inspector subprocess.

Deliberate, narrow exception to A5b's "no full PDF parser (PyMuPDF/pypdf)
in the main application process" rule: `pypdf` is imported HERE ONLY,
inside a standalone script meant to be invoked as a separate OS process
(`python -m app.services.pdf_inspector_subprocess <path>`), never imported
into the main FastAPI/worker process. This process is:

  - single-purpose: it inspects PDF *structure* (encryption flag, action
    dictionaries, embedded-file objects) and never calls
    `extract_text()`/`extract_images()`/rendering or any API that
    interprets page content;
  - resource-bounded: POSIX `resource.setrlimit` caps CPU time and
    address space before the untrusted file is ever opened;
  - no network/DB/secret access: it does not import `app.core.config`,
    the database layer, or any credential-bearing module;
  - fail-closed: every `pypdf` exception (malformed structure, unsupported
    feature, anything) is caught and reported as a FAILED result, never a
    raw traceback and never approximated as a PASS.

Contract with the parent process (`app.services.pdf_content_policy`):
  - Exactly ONE line of JSON is printed to stdout, and nothing else --
    `{"status": "CLEAN"}`, `{"status": "REJECTED", "reason_code": "..."}`,
    or `{"status": "FAILED", "reason_code": "PDF_INSPECTION_FAILED"}`.
  - Exit code 0 for CLEAN/REJECTED; a distinct nonzero exit code for
    FAILED (the parent treats a nonzero exit and a FAILED status line
    identically regardless).
  - The file path and any page content are NEVER printed, logged, or
    otherwise emitted by this process, on stdout or stderr.

This module intentionally has no dependency on the rest of the
application (no `app.core.config`, no logging framework) so it stays a
minimal, auditable, standalone script. Resource-limit values are read
directly from environment variables the parent sets, rather than
importing `app.core.config.Settings`.
"""

from __future__ import annotations

import json
import logging
import os
import sys

# Silence any logging pypdf itself might emit (e.g. structural-warning
# messages) so nothing beyond our single JSON line can reach stdout/stderr.
logging.getLogger("pypdf").setLevel(logging.CRITICAL)
logging.getLogger("pypdf").propagate = False

_EXIT_OK = 0
_EXIT_FAILED = 3

_DEFAULT_CPU_SECONDS = 10
_DEFAULT_MEMORY_BYTES = 512 * 1024 * 1024  # 512 MiB

_ACTIVE_CONTENT_ACTION_TYPES = {"/JS", "/JavaScript", "/Launch", "/URI"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _apply_resource_limits() -> None:
    """Apply conservative POSIX resource limits before opening the
    untrusted file. No-op on platforms without the `resource` module
    (e.g. Windows), matching this repo's existing
    Windows-dev-vs-POSIX-prod guard pattern (see quarantine_storage.py)."""
    if sys.platform == "win32":
        return
    try:
        import resource
    except ImportError:
        return

    cpu_seconds = _env_int("PDF_INSPECTOR_CPU_SECONDS", _DEFAULT_CPU_SECONDS)
    memory_bytes = _env_int("PDF_INSPECTOR_MEMORY_BYTES", _DEFAULT_MEMORY_BYTES)

    if hasattr(resource, "RLIMIT_CPU"):
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        except (ValueError, OSError):
            pass
    if hasattr(resource, "RLIMIT_AS"):
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError):
            pass


def _emit(status: str, reason_code: str | None = None) -> None:
    payload: dict[str, str] = {"status": status}
    if reason_code is not None:
        payload["reason_code"] = reason_code
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _action_dict_is_active_content(action: object) -> str | None:
    """Return a reason code if `action` (a pypdf action/dictionary-like
    object) is active content this policy rejects, else None."""
    try:
        action_type = action.get("/S")  # type: ignore[attr-defined]
    except (AttributeError, TypeError, KeyError):
        return None
    action_type_str = str(action_type) if action_type is not None else ""
    if action_type_str in _ACTIVE_CONTENT_ACTION_TYPES:
        return "PDF_ACTIVE_CONTENT"
    return None


def _catalog_has_open_action_or_aa(reader: object) -> bool:
    try:
        root = reader.root_object  # type: ignore[attr-defined]
    except Exception:
        return False
    for key in ("/OpenAction", "/AA"):
        try:
            if key in root:
                return True
        except TypeError:
            continue
    return False


def _catalog_has_embedded_files(reader: object) -> bool:
    """Metadata-only check: lists attachment *names* from the
    /Names/EmbeddedFiles tree without reading any attachment content."""
    try:
        for _ in reader.attachment_list:  # type: ignore[attr-defined]
            return True
    except Exception:
        return False
    return False


def _all_object_numbers(reader: object) -> set[int]:
    """Every indirect object number reachable from the file's
    cross-reference data -- both the classic offset-table xref
    (`reader.xref`) and, for files using cross-reference streams,
    objects packed inside compressed object streams
    (`reader.xref_objStm`). Metadata only: this collects object
    *numbers*, it does not resolve or read any object content."""
    numbers: set[int] = set()
    try:
        xref = reader.xref  # type: ignore[attr-defined]
        for generation_map in xref.values():
            numbers.update(generation_map.keys())
    except Exception:
        pass
    try:
        obj_stm = reader.xref_objStm  # type: ignore[attr-defined]
        numbers.update(obj_stm.keys())
    except Exception:
        pass
    return numbers


def _has_orphan_filespec_object(reader: object) -> bool:
    """Bounded scan of every indirect object in the file for a
    `/Type /Filespec` dictionary, catching a Filespec object that isn't
    linked from `/Names/EmbeddedFiles` or any page annotation (an
    orphan-object smuggling technique that both of those targeted checks
    miss). The subprocess's own CPU/memory rlimits and the parent's
    wall-clock timeout already bound the worst-case cost of walking a
    maliciously large file's object table."""
    try:
        object_numbers = _all_object_numbers(reader)
    except Exception:
        return False
    for number in object_numbers:
        try:
            obj = reader.get_object(number)  # type: ignore[attr-defined]
        except Exception:
            continue
        try:
            if obj.get("/Type") == "/Filespec":
                return True
        except (AttributeError, TypeError):
            continue
    return False


def _annot_is_file_attachment(annot: object) -> bool:
    try:
        if annot.get("/Subtype") == "/FileAttachment":  # type: ignore[attr-defined]
            return True
    except (AttributeError, TypeError):
        pass
    try:
        return "/FS" in annot  # type: ignore[operator]
    except TypeError:
        return False


def _pages_have_active_content_or_uri(reader: object) -> str | None:
    try:
        pages = reader.pages  # type: ignore[attr-defined]
    except Exception:
        return "PDF_INSPECTION_FAILED"
    for page in pages:
        try:
            annots = page.get("/Annots")
        except (AttributeError, TypeError):
            continue
        if not annots:
            continue
        for annot_ref in annots:
            try:
                annot = annot_ref.get_object()
            except Exception:
                continue
            if _annot_is_file_attachment(annot):
                return "PDF_EMBEDDED_FILE"
            try:
                action = annot.get("/A")
            except (AttributeError, TypeError):
                action = None
            if action is not None:
                reason = _action_dict_is_active_content(action)
                if reason is not None:
                    return reason
    return None


def inspect_pdf(path: str) -> tuple[str, str | None]:
    """Run all structural checks against `path`. Returns (status,
    reason_code). Never raises -- every pypdf failure is caught and
    reported as a FAILED status."""
    try:
        import pypdf

        reader = pypdf.PdfReader(path)

        if reader.is_encrypted:
            return "REJECTED", "PDF_ENCRYPTED"

        if _catalog_has_open_action_or_aa(reader):
            return "REJECTED", "PDF_ACTIVE_CONTENT"

        page_reason = _pages_have_active_content_or_uri(reader)
        if page_reason is not None:
            if page_reason == "PDF_INSPECTION_FAILED":
                return "FAILED", "PDF_INSPECTION_FAILED"
            return "REJECTED", page_reason

        if _catalog_has_embedded_files(reader):
            return "REJECTED", "PDF_EMBEDDED_FILE"

        if _has_orphan_filespec_object(reader):
            return "REJECTED", "PDF_EMBEDDED_FILE"

        return "CLEAN", None
    except Exception:
        return "FAILED", "PDF_INSPECTION_FAILED"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        _emit("FAILED", "PDF_INSPECTION_FAILED")
        return _EXIT_FAILED

    _apply_resource_limits()

    path = argv[1]
    status, reason_code = inspect_pdf(path)
    _emit(status, reason_code)
    return _EXIT_OK if status in ("CLEAN", "REJECTED") else _EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
