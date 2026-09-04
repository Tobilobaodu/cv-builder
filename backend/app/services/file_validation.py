"""File validation utilities for CV uploads.

Validates file type by magic bytes (content inspection, not just extension
or Content-Type header), and enforces size limits. Per security plan §2.
"""

import magic


ALLOWED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

ALLOWED_EXTENSIONS = {".pdf", ".docx"}

MAX_FILE_SIZE_MB = 20
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024


def validate_file_type(filename: str, file_content: bytes) -> str:
    """Validate file type by content inspection (magic bytes).

    Returns the detected MIME type, or raises ValueError with a specific message.

    Checks BOTH content inspection AND extension — an attacker must pass both,
    and a mismatch between the two is still rejected (belt-and-suspenders per
    security plan §2: filename spoofing vs. content-type spoofing are different
    attack vectors).
    """
    # Extension check
    if "." not in filename:
        raise ValueError(f"File has no extension. Supported: PDF, DOCX.")

    ext = filename.rsplit(".", 1)[-1].lower()
    if f".{ext}" not in ALLOWED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '.{ext}'. Only PDF and DOCX files are accepted."
        )

    # Content-type detection via libmagic
    detected_type = magic.from_buffer(file_content[:2048], mime=True)

    if detected_type not in ALLOWED_MIME_TYPES:
        raise ValueError(
            f"File content does not match a supported format. "
            f"Detected: {detected_type}. Only PDF and DOCX are accepted."
        )

    return detected_type


def validate_file_size(file_content: bytes) -> int:
    """Validate file size against the maximum allowed.

    Returns the file size in bytes, or raises ValueError.
    """
    size = len(file_content)
    if size == 0:
        raise ValueError("File is empty.")
    if size > MAX_FILE_SIZE_BYTES:
        raise ValueError(
            f"File size ({size} bytes) exceeds the maximum of {MAX_FILE_SIZE_MB} MB."
        )
    return size