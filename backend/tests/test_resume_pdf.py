"""Tests for rendering the tailored resume Markdown to PDF.

The converter handles only the subset the rewrite prompt emits, so these
pin that subset — and, more importantly, the escaping: the Markdown is
built from an uploaded CV and is rendered by a real browser engine inside
Gotenberg.
"""

import httpx
import pytest

from app.services.gotenberg_client import convert_html_to_pdf
from app.services.resume_pdf import (
    ResumePdfError,
    build_resume_html,
    markdown_to_html,
    render_resume_pdf,
)

RESUME_MD = """# TOBILOBA ODU
+447562695548 | oduoluwatobi@gmail.com | tobilobaodu.com

## Professional Summary
Product designer with **seven years** of experience.

## Professional Experience
### UX DESIGN MANAGER - OSB GROUP
APRIL 2022 - PRESENT
- Designed and delivered onboarding flows.
- Ran usability studies across seven brands.
"""


class TestMarkdownToHtml:
    def test_headings_by_level(self):
        out = markdown_to_html("# One\n## Two\n### Three")
        assert "<h1>One</h1>" in out
        assert "<h2>Two</h2>" in out
        assert "<h3>Three</h3>" in out

    def test_bullets_become_one_list(self):
        out = markdown_to_html("- First\n- Second")
        assert out.count("<ul>") == 1 and out.count("</ul>") == 1
        assert out.count("<li>") == 2

    def test_a_blank_line_closes_the_list(self):
        out = markdown_to_html("- First\n\nParagraph\n\n- Second")
        assert out.count("<ul>") == 2

    def test_bold_and_italic(self):
        out = markdown_to_html("Led **seven** teams and *shipped* fast.")
        assert "<strong>seven</strong>" in out
        assert "<em>shipped</em>" in out

    def test_links(self):
        out = markdown_to_html("[Portfolio](https://tobilobaodu.com)")
        assert '<a href="https://tobilobaodu.com">Portfolio</a>' in out

    def test_plain_lines_become_paragraphs(self):
        out = markdown_to_html("APRIL 2022 - PRESENT")
        assert out == "<p>APRIL 2022 - PRESENT</p>"

    def test_horizontal_rule_is_not_a_bullet(self):
        out = markdown_to_html("---")
        assert "<hr />" in out and "<li>" not in out

    def test_nothing_is_dropped(self):
        # Unrecognised syntax degrades to a paragraph rather than vanishing.
        out = markdown_to_html("> A blockquote we do not support")
        assert "blockquote we do not support" in out

    def test_empty_input(self):
        assert markdown_to_html("") == ""

    def test_realistic_resume(self):
        out = markdown_to_html(RESUME_MD)
        assert "<h1>TOBILOBA ODU</h1>" in out
        assert "<h3>UX DESIGN MANAGER - OSB GROUP</h3>" in out
        assert out.count("<li>") == 2


class TestEscaping:
    def test_script_tags_in_cv_content_are_escaped(self):
        out = markdown_to_html("- Ran <script>alert(1)</script> studies.")
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_ampersands_are_escaped(self):
        assert "&amp;" in markdown_to_html("- Research & Design")

    def test_a_full_document_never_leaks_raw_markup(self):
        html = build_resume_html("# <img src=x onerror=alert(1)>")
        assert "<img" not in html
        assert "onerror" in html  # escaped as text, not as an attribute
        assert "&lt;img" in html

    def test_link_href_is_quote_escaped(self):
        out = markdown_to_html('[x](https://e.com/")')
        assert '"' not in out.split("href=")[1].split(">")[0][1:-1]


class TestDocumentShell:
    def test_is_self_contained(self):
        # Gotenberg has no egress, so an external asset would silently fail
        # and the PDF would render in a default serif.
        html = build_resume_html(RESUME_MD)
        assert "<style>" in html
        assert "http://" not in html.split("<body>")[0]
        assert "https://" not in html.split("<body>")[0]

    def test_title_is_escaped(self):
        html = build_resume_html(RESUME_MD, title="<b>CV</b>")
        assert "<title>&lt;b&gt;CV&lt;/b&gt;</title>" in html


class TestRenderResumePdf:
    def _client(self, handler):
        return httpx.Client(transport=httpx.MockTransport(handler))

    def test_posts_html_to_the_chromium_route(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = request.content
            return httpx.Response(200, content=b"%PDF-1.7 fake")

        with self._client(handler) as client:
            pdf = render_resume_pdf(RESUME_MD, client=client)

        assert pdf == b"%PDF-1.7 fake"
        assert seen["url"].endswith("/forms/chromium/convert/html")
        assert b"index.html" in seen["body"]
        assert b"TOBILOBA ODU" in seen["body"]

    def test_empty_markdown_is_rejected_before_any_call(self):
        def handler(request):  # pragma: no cover - must not be reached
            raise AssertionError("Gotenberg should not be called")

        with self._client(handler) as client:
            with pytest.raises(ResumePdfError, match="no CV content"):
                render_resume_pdf("   ", client=client)

    def test_oversized_markdown_is_rejected(self):
        def handler(request):  # pragma: no cover - must not be reached
            raise AssertionError("Gotenberg should not be called")

        with self._client(handler) as client:
            with pytest.raises(ResumePdfError, match="too long"):
                render_resume_pdf("x" * 60_001, client=client)

    def test_a_gotenberg_failure_becomes_a_caller_safe_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, content=b"boom")

        with self._client(handler) as client:
            with pytest.raises(ResumePdfError, match="couldn't build the PDF"):
                render_resume_pdf(RESUME_MD, client=client)

    def test_a_transport_error_becomes_a_caller_safe_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("gotenberg unreachable")

        with self._client(handler) as client:
            with pytest.raises(ResumePdfError, match="couldn't build the PDF"):
                render_resume_pdf(RESUME_MD, client=client)


class TestConvertHtmlToPdf:
    def test_sends_print_margins_and_background(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = request.content
            return httpx.Response(200, content=b"%PDF-")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            convert_html_to_pdf("<html></html>", client=client)

        assert b"printBackground" in seen["body"]
        assert b"marginTop" in seen["body"]

    def test_a_caller_supplied_client_is_not_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"%PDF-")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        convert_html_to_pdf("<html></html>", client=client)
        assert not client.is_closed
        client.close()
