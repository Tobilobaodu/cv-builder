"""Tests for the job-post HTML stripper.

ssrf_safe_fetch returns the response body verbatim, so before this existed
a job post fetched from a URL was stored, displayed and sent to the model as
raw markup — including the whole of any inline <script>.
"""

from app.services.html_to_text import html_to_text, looks_like_html


class TestLooksLikeHtml:
    def test_detects_a_document(self):
        assert looks_like_html("<!doctype html><html><body>hi</body></html>")

    def test_detects_a_fragment(self):
        assert looks_like_html("<div class='posting'>Senior Designer</div>")

    def test_plain_text_is_not_html(self):
        assert not looks_like_html("Senior Designer\nRequires 5+ years")

    def test_comparison_operators_are_not_html(self):
        # A posting saying "salary > 60k" or "team of < 10" must not be
        # mistaken for markup and run through the stripper.
        assert not looks_like_html("Salary > 60k, team of < 10 people")


class TestHtmlToText:
    def test_drops_tags_and_decodes_entities(self):
        out = html_to_text("<p>Design &amp; research</p>")
        assert out == "Design & research"

    def test_script_body_is_removed_entirely(self):
        # The failure this whole module exists to prevent: <script> contents
        # are text nodes, so a naive tag-stripper emits them as prose.
        out = html_to_text(
            "<body><p>Real copy</p>"
            "<script>var tracking = {id: 42}; document.write('junk');</script>"
            "</body>"
        )
        assert "Real copy" in out
        assert "tracking" not in out
        assert "document.write" not in out

    def test_style_body_is_removed_entirely(self):
        out = html_to_text("<style>.hero{color:#fff}</style><p>Copy</p>")
        assert out == "Copy"
        assert "color" not in out

    def test_list_items_become_bullets_on_their_own_lines(self):
        out = html_to_text("<ul><li>Figma</li><li>Design systems</li></ul>")
        assert out == "- Figma\n- Design systems"

    def test_block_structure_survives(self):
        # Blocks are separated by a blank line, so the section headings a
        # job post relies on stay visually distinct rather than running
        # together into one paragraph.
        out = html_to_text(
            "<h2>Requirements</h2><p>Five years.</p><p>Figma.</p>"
        )
        assert out == "Requirements\n\nFive years.\n\nFigma."

    def test_blank_lines_never_run_more_than_two_deep(self):
        out = html_to_text("<div><div><div><p>Copy</p></div></div></div><p>More</p>")
        assert "\n\n\n" not in out

    def test_nbsp_becomes_a_normal_space(self):
        assert html_to_text("<p>Senior&nbsp;Designer</p>") == "Senior Designer"

    def test_whitespace_is_collapsed(self):
        out = html_to_text("<p>Senior     Product\t\tDesigner</p>")
        assert out == "Senior Product Designer"

    def test_plain_text_passes_through_unchanged(self):
        raw = "Senior Designer\n- Figma\n- Salary < 60k"
        assert html_to_text(raw) == raw

    def test_empty_input_is_returned_as_is(self):
        assert html_to_text("") == ""

    def test_a_page_that_strips_to_nothing_keeps_its_body(self):
        # Losing the body outright would be worse than keeping markup: the
        # user can still read and edit it.
        raw = "<html><head><script>var a=1</script></head><body></body></html>"
        assert html_to_text(raw) == raw

    def test_malformed_markup_does_not_raise(self):
        out = html_to_text("<div><p>Unclosed <b>bold<div>Next</p>")
        assert "Unclosed" in out and "Next" in out

    def test_nested_dropped_tags_do_not_end_suppression_early(self):
        # A <svg> inside a <script> string, or nesting of the same tag,
        # must not re-enable output halfway through.
        out = html_to_text(
            "<body><script>if (a<b) { }</script><p>Kept</p></body>"
        )
        assert "Kept" in out

    def test_realistic_posting(self):
        html = (
            "<!doctype html><html><head><title>Careers</title>"
            "<style>body{margin:0}</style></head><body>"
            "<nav><a href='/'>Home</a></nav>"
            "<h1>Senior Product Designer</h1>"
            "<p>You will own end&ndash;to&ndash;end design.</p>"
            "<h2>Requirements</h2>"
            "<ul><li>5+ years</li><li>Figma</li><li>WCAG 2.1 AA</li></ul>"
            "<script>analytics.track('view');</script></body></html>"
        )
        out = html_to_text(html)
        assert "Senior Product Designer" in out
        assert "- WCAG 2.1 AA" in out
        assert "analytics" not in out
        assert "margin" not in out
        assert "<" not in out
