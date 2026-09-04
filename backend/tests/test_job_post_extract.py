"""Tests for reducing a fetched careers page to the posting itself.

Built against a real failure: a Teamtailor posting stripped to ~6,200
characters of which roughly a third was the job — the rest cookie-consent
copy, a career menu, employee login links, colleague profiles, an
"About us" blurb and an applicant-tracking footer.
"""

from app.services.job_post_extract import extract_job_text

# A page shaped like the real one: consent dialog, nav, the posting, then
# colleagues and a footer. Only the middle should survive.
NOISY_PAGE = """<!doctype html><html><head><title>Careers</title>
<style>.x{color:red}</style></head><body>
<div id="cookie-consent-root">
  <p>This website uses cookies to ensure you get the best experience.</p>
  <div><button>Accept all cookies</button><button>Decline</button></div>
</div>
<nav aria-label="Career menu"><a href="/">Home</a><a href="/jobs">Jobs</a></nav>
<header><a href="/login">Log in as employee</a></header>
<main>
  <h1>Delivery Operations Manager</h1>
  <h2>About the role</h2>
  <p>You will sit within the Portfolio function and own operational
     processes, governance and reporting across the portfolio, working
     closely with the Head of Portfolio on delivery consistency.</p>
  <h2>Key responsibilities</h2>
  <ul>
    <li><p>Lead day-to-day operational execution of the function.</p></li>
    <li><p>Administer Statements of Work and commercial documentation.</p></li>
  </ul>
</main>
<section id="colleagues-module"><h3>Colleagues</h3><p>Raul Vasir</p></section>
<footer><p>Applicant tracking system by Teamtailor</p></footer>
<script>analytics.track('view');</script></body></html>"""

JOB_POSTING_LD = """<!doctype html><html><head>
<script type="application/ld+json">
{
  "@context": "http://schema.org",
  "@type": "JobPosting",
  "title": "Delivery Operations Manager",
  "employmentType": "FULL_TIME",
  "datePosted": "2026-07-16T16:42:13+01:00",
  "hiringOrganization": {"@type": "Organization", "name": "Tecknuovo"},
  "jobLocation": [{"@type": "Place", "address": {
      "streetAddress": "6 St Andrew St", "addressLocality": "London",
      "postalCode": "EC4A 3AE", "addressCountry": "GB",
      "@type": "PostalAddress"}}],
  "description": "&lt;h3&gt;About the role&lt;/h3&gt;&lt;p&gt;You will sit within the Portfolio function, owning operational processes, governance and reporting across the portfolio and working closely with the Head of Portfolio.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Lead day-to-day operational execution&lt;/li&gt;&lt;li&gt;Administer Statements of Work&lt;/li&gt;&lt;/ul&gt;"
}
</script></head><body>
<div class="cookie-banner"><p>This website uses cookies.</p></div>
<nav><a href="/">Home</a></nav>
<p>Loading application form</p>
<footer>Applicant tracking system by Teamtailor</footer>
</body></html>"""


class TestJsonLdTier:
    def test_uses_the_structured_posting_when_present(self):
        out = extract_job_text(JOB_POSTING_LD)
        assert "About the role" in out
        assert "Lead day-to-day operational execution" in out

    def test_header_carries_title_employer_location_and_type(self):
        out = extract_job_text(JOB_POSTING_LD)
        head = out.splitlines()[:4]
        assert head[0] == "Delivery Operations Manager"
        assert head[1] == "Tecknuovo"
        assert head[2] == "London, GB"
        assert head[3] == "Full Time"

    def test_page_furniture_is_absent(self):
        out = extract_job_text(JOB_POSTING_LD)
        for noise in ("cookies", "Applicant tracking system", "Loading application form"):
            assert noise not in out

    def test_double_escaped_description_is_decoded(self):
        # Teamtailor stores the description HTML-escaped inside the JSON
        # string, so &lt;h3&gt; has to be unescaped before it can be parsed.
        out = extract_job_text(JOB_POSTING_LD)
        assert "&lt;" not in out and "<h3>" not in out

    def test_graph_wrapped_posting_is_found(self):
        page = JOB_POSTING_LD.replace(
            '{\n  "@context": "http://schema.org",\n  "@type": "JobPosting",',
            '{"@context": "http://schema.org", "@graph": [{"@type": "JobPosting",',
        ).replace("}\n</script>", "}]}\n</script>")
        out = extract_job_text(page)
        assert "About the role" in out

    def test_a_stub_posting_falls_through_to_the_page(self):
        # An empty or near-empty description must not replace a usable
        # whole-page extraction with nothing.
        stub = (
            '<html><head><script type="application/ld+json">'
            '{"@type":"JobPosting","title":"X","description":"See website"}'
            "</script></head><body><main><h1>Senior Designer</h1>"
            "<p>" + ("Real posting copy. " * 20) + "</p></main></body></html>"
        )
        out = extract_job_text(stub)
        assert "Real posting copy." in out

    def test_malformed_json_ld_does_not_break_extraction(self):
        page = (
            '<html><head><script type="application/ld+json">{not json,,}</script>'
            "</head><body><main><p>" + ("Posting copy. " * 30) + "</p></main></body></html>"
        )
        out = extract_job_text(page)
        assert "Posting copy." in out


class TestFallbackTiers:
    def test_main_subtree_is_preferred_over_the_whole_page(self):
        out = extract_job_text(NOISY_PAGE)
        assert "Delivery Operations Manager" in out
        assert "Key responsibilities" in out
        assert "Administer Statements of Work" in out

    def test_consent_nav_colleagues_and_footer_are_dropped(self):
        out = extract_job_text(NOISY_PAGE)
        for noise in (
            "cookies",
            "Accept all cookies",
            "Career menu",
            "Log in as employee",
            "Colleagues",
            "Raul Vasir",
            "Applicant tracking system",
            "analytics",
        ):
            assert noise not in out, f"{noise!r} survived extraction"

    def test_bullets_stay_on_one_line_each(self):
        # <li><p>text</p></li> used to render the marker on its own line,
        # which reads as a stray dash and hides the list structure.
        out = extract_job_text(NOISY_PAGE)
        assert "- Lead day-to-day operational execution of the function." in out

    def test_page_without_main_still_yields_the_posting(self):
        page = (
            "<html><body><nav>Home</nav>"
            "<h1>Senior Product Designer</h1>"
            "<p>" + ("We need a designer. " * 20) + "</p>"
            "<footer>Privacy</footer></body></html>"
        )
        out = extract_job_text(page)
        assert "Senior Product Designer" in out
        assert "We need a designer." in out
        assert "Home" not in out
        assert "Privacy" not in out


class TestPassthrough:
    def test_plain_text_is_untouched(self):
        raw = "Senior Designer\n- Figma\n- Salary < 60k"
        assert extract_job_text(raw) == raw

    def test_empty_input(self):
        assert extract_job_text("") == ""
