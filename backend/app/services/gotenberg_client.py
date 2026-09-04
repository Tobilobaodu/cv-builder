"""Thin HTTP client wrapper for Gotenberg's docx-to-pdf conversion route.

Deliberately pulled out of worker_jobs.py into its own small module: at the
time this split was made, worker_jobs.py transitively imported docling via
app.extraction.parser_interface, which wasn't installed outside the Docker
image the workers run in, so nothing importable only via worker_jobs.py was
testable from the host venv. Docling was later decommissioned (see
decommissioned/README.md) and that import chain no longer exists, but the
split is still worth keeping: this module has no heavy dependencies, so
httpx.MockTransport can exercise the real request/response handling
directly, without a live Gotenberg container or the full worker module.
"""

from __future__ import annotations

import httpx

from app.core.config import settings

_DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def convert_html_to_pdf(
    html: str,
    *,
    client: httpx.Client | None = None,
    margin_inches: float = 0.6,
) -> bytes:
    """Convert a self-contained HTML document to PDF via Gotenberg's
    Chromium route.

    Used by the single-call rewrite flow, which produces Markdown rather
    than a docx: going Markdown -> HTML -> Chromium keeps full control of
    the print styling and avoids a LibreOffice round-trip through a
    document format nothing else in that flow uses.

    The HTML must be self-contained — Gotenberg runs on the `no_internet`
    network, so any external stylesheet, font or image simply fails to
    load. Same client-ownership contract as convert_docx_to_pdf.
    """
    owns_client = client is None
    http_client = client or httpx.Client(
        timeout=settings.gotenberg_request_timeout_seconds
    )
    try:
        response = http_client.post(
            f"{settings.gotenberg_url}/forms/chromium/convert/html",
            files={"files": ("index.html", html.encode("utf-8"), "text/html")},
            data={
                "marginTop": str(margin_inches),
                "marginBottom": str(margin_inches),
                "marginLeft": str(margin_inches),
                "marginRight": str(margin_inches),
                "printBackground": "true",
            },
        )
        response.raise_for_status()
        return response.content
    finally:
        if owns_client:
            http_client.close()


def convert_docx_to_pdf(docx_bytes: bytes, *, client: httpx.Client | None = None) -> bytes:
    """Converts docx bytes to pdf bytes via Gotenberg's LibreOffice
    conversion route. Raises httpx.HTTPStatusError on a non-2xx response
    — the caller (worker_jobs.py::process_export_pdf) is responsible for
    catching/retrying, same as any other infra call in this codebase.

    A caller-supplied client (e.g. one built with an httpx.MockTransport)
    is used as-is and never closed here — only a client this function
    creates itself gets closed.
    """
    owns_client = client is None
    http_client = client or httpx.Client(timeout=settings.gotenberg_request_timeout_seconds)
    try:
        response = http_client.post(
            f"{settings.gotenberg_url}/forms/libreoffice/convert",
            files={"files": ("source.docx", docx_bytes, _DOCX_CONTENT_TYPE)},
        )
        response.raise_for_status()
        return response.content
    finally:
        if owns_client:
            http_client.close()
