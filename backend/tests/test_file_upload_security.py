"""Adversarial file-upload security tests — security-plan §2 "How to test".

The security-plan audit found zero tests referencing app/services/file_validation.py,
app/services/malware_scan.py, or app/core/storage.py::generate_storage_key() despite
all three existing. These tests close that gap, one case per §2 bullet.

Magic-byte / extraction-timeout tests stub `magic` and `docling` at module level —
the host Windows venv has no working libmagic (segfaults the interpreter) and no
docling install, so the real libraries are only exercised inside the Docker image.
This is the same stub pattern used by test_auth_endpoints.py (magic) and
test_ats_check_live.py (docling). The EICAR test is a genuine live test against the
real ClamAV container, not stubbed.
"""

import asyncio
import sys
import time
import types

import pytest

# ── Stub magic (libmagic native lib not available on host Windows) ─────────
# Faithful signature-based stub: returns the same MIME type real libmagic
# would for PDF / PNG / DOCX / plain-text inputs. Unconditionally overwrite
# `from_buffer` (rather than guarding on `not in sys.modules`) — an earlier
# test in the full suite may already have stubbed `magic` with a coarser stub,
# and file_validation's `import magic` reference resolves to this same module
# object, so patching the attribute here wins regardless of import order.
if "magic" not in sys.modules:
    sys.modules["magic"] = types.ModuleType("magic")

def _stub_from_buffer(buf, mime=False):
    b = bytes(buf)
    if b.startswith(b"%PDF"):
        return "application/pdf"
    if b.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if b.startswith(b"PK\x03\x04"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "text/plain"

_magic_mod = sys.modules["magic"]
_magic_mod.MagicException = Exception
_magic_mod.from_buffer = _stub_from_buffer
_magic_mod.from_file = lambda path, mime=False: "application/octet-stream"

# ── Stub docling (not installed on host; imported at module level by ───────
# docling_parser). DocumentConverter is replaced per-test for the timeout case.
if "docling" not in sys.modules:
    _base_models = types.ModuleType("docling.datamodel.base_models")

    class _InputFormat:
        PDF = object()
        DOCX = object()
        IMAGE = object()

    _base_models.InputFormat = _InputFormat

    _pipeline_options = types.ModuleType("docling.datamodel.pipeline_options")

    class _PdfPipelineOptions:
        do_ocr = False
        do_table_structure = True

    _pipeline_options.PdfPipelineOptions = _PdfPipelineOptions

    _document_converter = types.ModuleType("docling.document_converter")

    class _DocumentConverter:
        def __init__(self, format_options=None):
            pass

        def convert(self, source):
            raise NotImplementedError("stub — replaced per-test")

    _document_converter.DocumentConverter = _DocumentConverter

    class _PdfFormatOption:
        def __init__(self, pipeline_options=None):
            pass

    class _WordFormatOption:
        def __init__(self):
            pass

    _document_converter.PdfFormatOption = _PdfFormatOption
    _document_converter.WordFormatOption = _WordFormatOption

    _docling_core_io = types.ModuleType("docling_core.types.io")

    class _DocumentStream:
        def __init__(self, *args, **kwargs):
            pass

    _docling_core_io.DocumentStream = _DocumentStream

    sys.modules["docling"] = types.ModuleType("docling")
    sys.modules["docling.datamodel"] = types.ModuleType("docling.datamodel")
    sys.modules["docling.datamodel.base_models"] = _base_models
    sys.modules["docling.datamodel.pipeline_options"] = _pipeline_options
    sys.modules["docling.document_converter"] = _document_converter
    sys.modules["docling_core"] = types.ModuleType("docling_core")
    sys.modules["docling_core.types"] = types.ModuleType("docling_core.types")
    sys.modules["docling_core.types.io"] = _docling_core_io


from app.services.file_validation import validate_file_type, validate_file_size  # noqa: E402
from app.services.malware_scan import scan_file  # noqa: E402
from app.core.storage import generate_storage_key  # noqa: E402

# DECOMMISSIONED: the Docling imports and the convert-timeout test that
# used them moved to decommissioned/test_docling_convert_timeout.py
# when pipeline step 3 was retired. Everything else in this file (magic-byte
# validation, storage-key handling, EICAR/ClamAV) is unaffected and still runs.


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
)
# Standard EICAR anti-malware test signature — the safe, industry-standard
# test-malware string ClamAV is guaranteed to flag.
EICAR_BYTES = b'X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*'


# ── §2: Magic-byte spoofing ────────────────────────────────────────────────


def test_pdf_named_file_with_png_bytes_rejected():
    """A .pdf filename whose bytes are a PNG is rejected on content, not name."""
    with pytest.raises(ValueError) as exc:
        validate_file_type("resume.pdf", PNG_BYTES)
    msg = str(exc.value)
    assert "content" in msg.lower() or "Detected" in msg, (
        f"rejection must reference content inspection, got: {msg}"
    )


def test_pdf_named_file_with_plain_text_rejected():
    """A .pdf filename whose bytes are plain text is rejected."""
    with pytest.raises(ValueError):
        validate_file_type("resume.pdf", b"just some plain text, not a PDF")


def test_pdf_bytes_with_pdf_name_accepted():
    assert validate_file_type("resume.pdf", PDF_BYTES) == "application/pdf"


def test_unsupported_extension_rejected_even_for_valid_pdf_bytes():
    """Extension check is independent of content — an .exe name is rejected."""
    with pytest.raises(ValueError):
        validate_file_type("resume.exe", PDF_BYTES)


# ── §2: Size limits ────────────────────────────────────────────────────────


def test_oversized_file_rejected():
    with pytest.raises(ValueError) as exc:
        validate_file_size(b"x" * (20 * 1024 * 1024 + 1))
    assert "size" in str(exc.value).lower()


def test_empty_file_rejected():
    with pytest.raises(ValueError):
        validate_file_size(b"")


def test_file_at_exact_limit_accepted():
    assert validate_file_size(b"x" * (20 * 1024 * 1024)) == 20 * 1024 * 1024


# ── §2: Path-traversal filename ────────────────────────────────────────────


def test_storage_key_is_unrelated_to_filename():
    key = generate_storage_key("../../../etc/passwd.pdf")
    assert key.startswith("cvs/")
    assert ".." not in key
    assert "etc" not in key and "passwd" not in key
    # uuid4 hex (32 chars) + sanitized ".pdf" extension
    body = key[len("cvs/"):]
    assert body.endswith(".pdf")
    assert len(body.split(".")[0]) == 32


def test_storage_key_sanitizes_extension():
    key = generate_storage_key("x.../../../pdf")
    # extension is sanitized to alphanumeric only; no path separators leak in
    assert "/" not in key.split("cvs/", 1)[1]
    assert ".." not in key


# ── §2: Concurrent upload collision ────────────────────────────────────────


@pytest.mark.asyncio(loop_scope="function")
async def test_storage_keys_are_unique_under_concurrency():
    """20 iterations of parallel key generation never collide.

    generate_storage_key is uuid4-based (no shared mutable counter), so a
    collision would indicate a regression to a deterministic/stateful scheme.
    """
    for _ in range(20):
        keys = await asyncio.gather(
            *(asyncio.to_thread(generate_storage_key, "resume.pdf") for _ in range(2))
        )
        assert len(set(keys)) == 2, f"collision: {keys}"


# ── §2: EICAR malware-scan block (live ClamAV, not stubbed) ────────────────
#
# .env.local sets CLAMD_HOST=clamav (the Docker-internal hostname), which the
# host venv can't resolve — but docker-compose publishes 3310:3310, so the
# running ClamAV container IS reachable from the host at localhost:3310. These
# tests point scan_file() at localhost, matching how every other "live" test in
# this suite reaches Docker services (localhost port mappings), while the
# in-container hostname stays reserved for the worker itself.


def _clamav_available() -> bool:
    import clamd

    try:
        cd = clamd.ClamdNetworkSocket(host="localhost", port=3310, timeout=3)
        cd.ping()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _clamav_available(), reason="ClamAV container not reachable")
@pytest.mark.asyncio(loop_scope="function")
async def test_eicar_malware_blocked(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "clamd_host", "localhost")
    with pytest.raises(ValueError):
        await scan_file(EICAR_BYTES)


@pytest.mark.skipif(not _clamav_available(), reason="ClamAV container not reachable")
@pytest.mark.asyncio(loop_scope="function")
async def test_clean_file_passes_malware_scan(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "clamd_host", "localhost")
    assert await scan_file(PDF_BYTES) is True


# ── §2: Extraction timeout enforcement ─────────────────────────────────────
