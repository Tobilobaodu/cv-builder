from app.extraction.job_post_parser import RulesBasedJobPostParser


def test_glyphless_bullets_are_extracted():
    """Real postings copy-pasted from a webpage's <li> elements often have
    no bullet glyph at all — just indented plain lines. These must still
    be recognized as list items."""
    text = """Requirements:
Own major surfaces end-to-end
Turn clinical blood results into something understandable
Design for personalisation across different user needs
"""

    result = RulesBasedJobPostParser().parse(text)

    assert result.qualifications, "qualifications should be non-empty"
    assert len(result.qualifications) == 3
    assert "Own major surfaces end-to-end" in result.qualifications


def test_glyphless_prose_is_not_misclassified():
    """Ordinary prose sentences (ending in sentence-terminal punctuation)
    must not be treated as bullets just because there's no glyph — even
    when a section has multiple such lines."""
    text = """Requirements:
We are looking for someone who can grow with the team over time.
The role reports directly to the Head of Product.
"""

    result = RulesBasedJobPostParser().parse(text)

    assert not result.qualifications


def test_mixed_bullet_formats_are_extracted():
    text = """Requirements:
- 5+ years Python
1. AWS experience
• Docker
"""

    result = RulesBasedJobPostParser().parse(text)

    print("qualifications:", result.qualifications)
    print("responsibilities:", result.responsibilities)

    assert result.qualifications, "qualifications should be non-empty"

    qualifications = " ".join(result.qualifications).lower()
    assert "python" in qualifications
    assert "aws" in qualifications
    assert "docker" in qualifications