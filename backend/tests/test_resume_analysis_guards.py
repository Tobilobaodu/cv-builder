"""Guards that do not depend on the model obeying the prompt.

Moved from test_resume_rewrite_guards.py when the score/occupation logic
split out of resume_rewrite.py into resume_analysis.py (jbs-solution-
sheet.md S1). Same reported defect as before: a product-design CV scored
85/100 "Good match" against an HR People Experience Lead role.
"""

import pytest

from app.services import resume_analysis as ra

CV_NO_LOCATION = """TOBILOBA ODU
PRODUCT DESIGNER, UI/UX and RESEARCH
tobilobaodu.com | oduoluwatobi@gmail.com | +447562695548

PROFILE
Product Designer with seven years of experience across UX research, UI
design, design systems, and conversion focused digital optimisation.
"""


class TestLabelDerivation:
    @pytest.mark.parametrize(
        "score,expected",
        [
            (100.0, "Strong match"),
            (75.0, "Strong match"),
            (74.9, "Good match"),
            (50.0, "Good match"),
            (49.9, "Needs work"),
            (0.0, "Needs work"),
        ],
    )
    def test_thresholds(self, score, expected):
        assert ra._label_for(score) == expected


class TestCrossOccupationCap:
    def _run(self, monkeypatch, stats: dict):
        class _Result:
            data = {
                "matchNotes": ["A note."],
                "informationNeeded": [],
                "stats": stats,
            }
            prompt_tokens = 10
            completion_tokens = 10
            model = "test-model"

        monkeypatch.setattr(
            ra, "generate_structured", lambda **kwargs: _Result()
        )
        return ra.analyze_resume(
            cv_text=CV_NO_LOCATION, job_post_text="People Experience Lead. " * 10
        )

    def _stats(self, **over):
        base = {
            "cvOccupation": "Product Designer",
            "jobOccupation": "People / HR",
            "sameOccupation": False,
            "atsScore": 85,
            "matchLabel": "Good match",
            "matchedSkills": [],
            "transferableSkills": [],
            "missingSkills": [],
            "priorityKeywords": [],
        }
        base.update(over)
        return base

    def test_a_different_profession_is_capped(self, monkeypatch):
        # The exact reported case: 85/100 for a product designer against an
        # HR role. The model's own number is not trusted.
        out = self._run(monkeypatch, self._stats())
        assert out.stats["atsScore"] == 40.0
        assert out.stats["matchLabel"] == "Needs work"

    def test_the_cap_is_explained_in_the_notes(self, monkeypatch):
        out = self._run(monkeypatch, self._stats())
        assert "Different profession" in out.match_notes[0]
        assert "Product Designer" in out.match_notes[0]
        assert "People / HR" in out.match_notes[0]

    def test_a_low_cross_occupation_score_is_not_raised(self, monkeypatch):
        out = self._run(monkeypatch, self._stats(atsScore=12))
        assert out.stats["atsScore"] == 12.0

    def test_same_occupation_is_untouched(self, monkeypatch):
        out = self._run(
            monkeypatch,
            self._stats(
                sameOccupation=True,
                jobOccupation="Senior Product Designer",
                atsScore=88,
                matchLabel="Needs work",
            ),
        )
        assert out.stats["atsScore"] == 88.0
        # Label is re-derived, so it can never contradict the score.
        assert out.stats["matchLabel"] == "Strong match"
        assert not out.match_notes[0].startswith("Different profession")

    def test_out_of_range_scores_are_clamped(self, monkeypatch):
        out = self._run(monkeypatch, self._stats(sameOccupation=True, atsScore=140))
        assert out.stats["atsScore"] == 100.0
