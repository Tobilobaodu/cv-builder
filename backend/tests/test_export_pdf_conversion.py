"""Tests for app/services/gotenberg_client.py — the docx-to-pdf HTTP
call, mocked via httpx.MockTransport (no real Gotenberg container, no
docling dependency). A real Gotenberg round-trip is a manual live-
Docker-stack verification step, same as every prior sprint's live
walkthrough, not something to fake with a mock and call done.
"""

import httpx
import pytest

from app.services.gotenberg_client import convert_docx_to_pdf


def _client_returning(status_code: int, content: bytes, *, capture: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if capture is not None:
            capture["request"] = request
        return httpx.Response(status_code, content=content)
    return httpx.Client(transport=httpx.MockTransport(handler))


class TestConvertDocxToPdf:
    def test_returns_response_body_on_success(self):
        client = _client_returning(200, b"%PDF-1.4 fake pdf bytes")
        result = convert_docx_to_pdf(b"fake docx bytes", client=client)
        assert result == b"%PDF-1.4 fake pdf bytes"

    def test_sends_docx_as_multipart_file(self):
        capture: dict = {}
        client = _client_returning(200, b"pdf", capture=capture)
        convert_docx_to_pdf(b"fake docx bytes", client=client)
        request = capture["request"]
        assert request.method == "POST"
        assert request.url.path == "/forms/libreoffice/convert"
        assert b"fake docx bytes" in request.content
        assert b"source.docx" in request.content

    def test_raises_on_non_2xx_response(self):
        client = _client_returning(500, b"internal error")
        with pytest.raises(httpx.HTTPStatusError):
            convert_docx_to_pdf(b"fake docx bytes", client=client)

    def test_raises_on_4xx_response(self):
        client = _client_returning(400, b"bad request - not a valid docx")
        with pytest.raises(httpx.HTTPStatusError):
            convert_docx_to_pdf(b"fake docx bytes", client=client)

    def test_caller_supplied_client_is_not_closed(self):
        """A caller-managed client (e.g. reused across calls, or a test
        double) must be left open — only a client this function creates
        itself should ever be closed."""
        client = _client_returning(200, b"pdf")
        convert_docx_to_pdf(b"fake docx bytes", client=client)
        assert not client.is_closed
