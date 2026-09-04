"""'Who you are' must route to the requirements section, not description.

It previously appeared in both patterns; since section matching is
first-match-wins and 'description' was checked first, the 'requirements'
copy was unreachable dead code for any posting using this common heading.
"""

from app.extraction.job_post_parser import RulesBasedJobPostParser


def test_who_you_are_routes_to_requirements():
    text = """About the role

We're building something new.

Who you are

Around 4+ years of product design experience
Strong visual and interaction craft
"""
    result = RulesBasedJobPostParser().parse(text)

    assert result.qualifications, "qualifications should be populated from 'Who you are'"
    joined = " ".join(result.qualifications).lower()
    assert "product design experience" in joined
