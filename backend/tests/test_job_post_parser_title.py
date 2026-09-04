"""Title extraction should scan the true preamble, not a fixed lines[:5]
window — and must return None rather than guess when nothing confident
is found (e.g. a section heading picked up by mistake).

Note: real plain-text job postings typically render each paragraph as a
single long unwrapped line (no manual line breaks), so fixtures here use
one long line per paragraph to match that shape.
"""

from app.extraction.job_post_parser import RulesBasedJobPostParser


def test_title_found_past_long_preamble_paragraph():
    text = (
        "Acme Corp is a growing fintech company building tools for small "
        "businesses, founded in 2018 and now serving thousands of customers "
        "across the country with a focus on simplicity and trust in everything "
        "we do for our members every single day.\n"
        "\n"
        "Senior Backend Engineer\n"
        "\n"
        "Requirements:\n"
        "- 5+ years Python\n"
    )
    result = RulesBasedJobPostParser().parse(text)
    assert result.job_title == "Senior Backend Engineer"


def test_title_is_none_when_opening_lines_are_all_long_paragraphs():
    """When every preamble line is long prose and the first short line
    is actually a section heading, the parser must not fall back to
    picking that heading as the title."""
    text = (
        "Do Health is Voy's next chapter: a mobile-first preventative health "
        "product built as an independent startup, testing members' blood "
        "regularly and turning results into a personalised programme across "
        "four pillars: Eat, Move, Sleep, Relax.\n"
        "\n"
        "You'll join as the senior design voice on a small team, with no "
        "layer of management between you and the work in this role at all.\n"
        "\n"
        "What you'll do\n"
        "\n"
        "- Own major surfaces end-to-end\n"
        "- Turn clinical blood results into something understandable\n"
    )
    result = RulesBasedJobPostParser().parse(text)
    assert result.job_title != "What you'll do"
    assert result.job_title is None
