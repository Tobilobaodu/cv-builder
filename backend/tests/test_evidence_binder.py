"""Unit tests for evidence_binder.py — pure functions, no DB, no LLM.

The module this tests is the direct implementation of
10-security-plan.md's evidence-reference content-verification
requirement, so these tests are the primary proof that requirement is
actually satisfied, not just documented.
"""
from types import SimpleNamespace

from app.extraction.evidence_binder import (
    EvidenceCandidate,
    EXPERIENCE,
    EDUCATION,
    SKILL,
    CERTIFICATION,
    PROJECT,
    ANSWER,
    build_candidate_pool,
    build_answer_candidates,
    bind_evidence_pool,
    count_experience_relevance,
    count_project_relevance,
    verify_claim_against_evidence,
)


_UNSET = object()


def _exp(id="exp1", title="Software Engineer", company="Acme Corp",
         bullets=_UNSET, technologies=_UNSET):
    # `is _UNSET`, not `or` — an explicitly-passed empty list must stay
    # empty, not silently fall back to the default fixture content (a
    # real bug caught during test authoring: `technologies or [...]`
    # treats `[]` as falsy and replaces it with the default).
    if bullets is _UNSET:
        bullets = ["Built REST APIs serving 2M requests/day using Python and Docker"]
    if technologies is _UNSET:
        technologies = ["Python", "Docker"]
    return SimpleNamespace(id=id, title=title, company=company, bullets=bullets, technologies=technologies)


def _skill(id="sk1", skill_name="Python"):
    return SimpleNamespace(id=id, skill_name=skill_name)


def _edu(id="ed1", institution="MIT", degree="BSc", field="Computer Science"):
    return SimpleNamespace(id=id, institution=institution, degree=degree, field=field)


def _cert(id="cert1", name="AWS Certified Solutions Architect", issuer="Amazon"):
    return SimpleNamespace(id=id, name=name, issuer=issuer)


def _project(id="proj1", name="Finance Tracker", description="A budgeting app",
             bullets=_UNSET, technologies=_UNSET):
    if bullets is _UNSET:
        bullets = ["Reduced manual entry by 80%"]
    if technologies is _UNSET:
        technologies = ["React", "Node"]
    return SimpleNamespace(id=id, name=name, description=description,
                            bullets=bullets, technologies=technologies)


def _answer(id="ans1", question_id="q1", answer_text="I love the mission."):
    return SimpleNamespace(id=id, question_id=question_id, answer_text=answer_text)


def _question(question_text="Why this role?"):
    return SimpleNamespace(question_text=question_text)


def _evidence(support_level, requirement_text, requirement_type="required",
              suggestion=None, warning=None):
    return SimpleNamespace(
        support_level=support_level, requirement_text=requirement_text,
        requirement_type=requirement_type, suggestion=suggestion, warning=warning,
    )


class TestBuildCandidatePool:

    def test_builds_one_candidate_per_row(self):
        pool = build_candidate_pool([_exp()], [_edu()], [_skill()])
        assert len(pool) == 3
        types = {c.row_type for c in pool}
        assert types == {EXPERIENCE, EDUCATION, SKILL}

    def test_experience_searchable_text_includes_bullets_and_technologies(self):
        pool = build_candidate_pool(
            [_exp(bullets=["Led migration to Kubernetes"], technologies=["Kubernetes"])],
            [], [],
        )
        assert "Kubernetes" in pool[0].searchable_text
        assert "Led migration" in pool[0].searchable_text

    def test_skill_searchable_text_is_just_the_name(self):
        pool = build_candidate_pool([], [], [_skill(skill_name="Docker")])
        assert pool[0].searchable_text == "Docker"

    def test_none_fields_do_not_crash(self):
        exp = SimpleNamespace(id="e1", title=None, company=None, bullets=None, technologies=None)
        pool = build_candidate_pool([exp], [], [])
        assert pool[0].searchable_text == ""

    def test_certification_and_project_items_are_pooled(self):
        """Additive-only extension: existing 3-positional-arg call sites
        (every other test in this class) keep working unchanged — the new
        params are keyword-only with a None default."""
        pool = build_candidate_pool(
            [], [], [],
            certification_items=[_cert()], project_items=[_project()],
        )
        assert len(pool) == 2
        types = {c.row_type for c in pool}
        assert types == {CERTIFICATION, PROJECT}

    def test_certification_searchable_text_includes_issuer(self):
        pool = build_candidate_pool([], [], [], certification_items=[_cert(name="CKA", issuer="CNCF")])
        assert pool[0].searchable_text == "CKA CNCF"

    def test_project_searchable_text_includes_bullets_and_technologies(self):
        pool = build_candidate_pool(
            [], [], [],
            project_items=[_project(bullets=["Built a REST API"], technologies=["FastAPI"])],
        )
        assert "Built a REST API" in pool[0].searchable_text
        assert "FastAPI" in pool[0].searchable_text

    def test_omitting_new_params_defaults_to_empty(self):
        pool = build_candidate_pool([_exp()], [_edu()], [_skill()])
        assert len(pool) == 3


class TestBuildAnswerCandidates:
    """Cover-letter-specific (Sprint 4): a candidate's own Q&A answers as
    real, citable evidence, unconditionally includable (never relevance-
    filtered the way CV rows are)."""

    def test_pairs_answer_with_question_text(self):
        candidates = build_answer_candidates(
            {"q1": _question("Why are you interested in this role?")},
            [_answer(id="a1", question_id="q1", answer_text="I love the mission.")],
        )
        assert len(candidates) == 1
        assert candidates[0].row_type == ANSWER
        assert candidates[0].row_id == "a1"
        assert "Why are you interested in this role?" in candidates[0].searchable_text
        assert "I love the mission." in candidates[0].searchable_text

    def test_missing_question_does_not_crash(self):
        """An answer whose question_id doesn't resolve (shouldn't happen
        in practice, but never guess/crash) still yields a candidate,
        just without question-text pairing."""
        candidates = build_answer_candidates(
            {}, [_answer(id="a1", question_id="missing", answer_text="Some answer.")],
        )
        assert len(candidates) == 1
        assert candidates[0].searchable_text == "Some answer."

    def test_multiple_answers_each_become_a_candidate(self):
        candidates = build_answer_candidates(
            {"q1": _question("Q1?"), "q2": _question("Q2?")},
            [_answer(id="a1", question_id="q1"), _answer(id="a2", question_id="q2")],
        )
        assert {c.row_id for c in candidates} == {"a1", "a2"}


class TestBindEvidencePool:

    def test_supported_skill_binds_to_real_skill_row(self):
        pool = build_candidate_pool([], [], [_skill(id="sk1", skill_name="Python")])
        items = [_evidence("supported", "Python")]
        bound = bind_evidence_pool(items, pool)
        assert [c.row_id for c in bound] == ["sk1"]

    def test_partially_supported_binds_via_substring_in_experience(self):
        pool = build_candidate_pool(
            [_exp(id="exp1", bullets=["Built REST APIs at scale"])], [], [],
        )
        items = [_evidence("partially_supported", "REST APIs")]
        bound = bind_evidence_pool(items, pool)
        assert [c.row_id for c in bound] == ["exp1"]

    def test_unsupported_never_binds(self):
        pool = build_candidate_pool([], [], [_skill(skill_name="Kubernetes")])
        items = [_evidence("unsupported", "Kubernetes")]
        assert bind_evidence_pool(items, pool) == []

    def test_contradictory_never_binds(self):
        """The most security-relevant case: contradictory evidence must
        never enter the generation pool, full stop — this is what
        structurally guarantees 09-test-plan.md §6's "never silently
        resolved" requirement, not a convention generation code has to
        remember to respect."""
        pool = build_candidate_pool([_exp(id="exp1")], [], [])
        items = [_evidence("contradictory", "Software Engineer")]
        assert bind_evidence_pool(items, pool) == []

    def test_unclear_never_binds(self):
        pool = build_candidate_pool([_exp(id="exp1")], [], [])
        items = [_evidence("unclear", "Software Engineer")]
        assert bind_evidence_pool(items, pool) == []

    def test_no_matching_row_yields_no_candidate_not_an_error(self):
        pool = build_candidate_pool([], [], [_skill(skill_name="Python")])
        items = [_evidence("supported", "Rust")]
        assert bind_evidence_pool(items, pool) == []

    def test_dedup_across_multiple_evidence_items(self):
        pool = build_candidate_pool(
            [_exp(id="exp1", bullets=["Built REST APIs using Python and Docker"],
                  technologies=["Python", "Docker"])],
            [], [],
        )
        items = [
            _evidence("supported", "Python"),
            _evidence("partially_supported", "Docker"),
        ]
        bound = bind_evidence_pool(items, pool)
        # Same experience row matches both requirements — must appear once.
        assert [c.row_id for c in bound] == ["exp1"]

    def test_empty_requirement_text_is_skipped(self):
        pool = build_candidate_pool([], [], [_skill(skill_name="Python")])
        items = [_evidence("supported", "")]
        assert bind_evidence_pool(items, pool) == []

    def test_certification_matches_via_exact_containment_like_skill(self):
        """Certifications get the same equality/containment treatment as
        skills (a credential's name matters as a whole), not the
        education/project fuzzy-substring path."""
        pool = build_candidate_pool(
            [], [], [], certification_items=[_cert(id="c1", name="AWS Certified Solutions Architect")],
        )
        items = [_evidence("supported", "AWS Certified Solutions Architect")]
        bound = bind_evidence_pool(items, pool)
        assert [c.row_id for c in bound] == ["c1"]

    def test_project_matches_via_substring_containment(self):
        pool = build_candidate_pool(
            [], [], [], project_items=[_project(id="p1", description="A budgeting app using React")],
        )
        items = [_evidence("partially_supported", "React")]
        bound = bind_evidence_pool(items, pool)
        assert [c.row_id for c in bound] == ["p1"]


class TestCountExperienceRelevance:

    def test_ranks_more_referenced_item_higher(self):
        exp1 = _exp(id="exp1", bullets=["Built APIs"], technologies=["Python", "Docker"])
        exp2 = _exp(id="exp2", title="Intern", company="Startup", bullets=["Helped with QA"], technologies=[])
        pool = build_candidate_pool([exp1, exp2], [], [])
        items = [
            _evidence("supported", "Python"),
            _evidence("partially_supported", "Docker"),
            _evidence("partially_supported", "APIs"),
        ]
        counts = count_experience_relevance(items, pool)
        assert counts == {"exp1": 3}
        assert "exp2" not in counts

    def test_ignores_ineligible_support_levels(self):
        pool = build_candidate_pool([_exp(id="exp1")], [], [])
        items = [_evidence("unsupported", "Software Engineer")]
        assert count_experience_relevance(items, pool) == {}


class TestCountProjectRelevance:

    def test_ranks_more_referenced_project_higher(self):
        proj1 = _project(id="p1", description="Built a React app", technologies=["React"])
        proj2 = _project(id="p2", name="CLI Tool", description="A CLI utility", technologies=[])
        pool = build_candidate_pool([], [], [], project_items=[proj1, proj2])
        items = [
            _evidence("supported", "React"),
            _evidence("partially_supported", "Built a React app"),
        ]
        counts = count_project_relevance(items, pool)
        assert counts == {"p1": 2}
        assert "p2" not in counts

    def test_ignores_ineligible_support_levels(self):
        pool = build_candidate_pool([], [], [], project_items=[_project(id="p1")])
        items = [_evidence("unsupported", "Finance Tracker")]
        assert count_project_relevance(items, pool) == {}


class TestVerifyClaimAgainstEvidence:

    _evidence_texts = [
        "Software Engineer Acme Corp Built REST APIs serving 2M requests/day using Python and Docker",
        "Python",
    ]

    def test_faithful_claim_passes(self):
        result = verify_claim_against_evidence(
            "Built REST APIs handling 2M requests per day with Python and Docker.",
            self._evidence_texts, overlap_threshold=0.35,
        )
        assert result.passed

    def test_faithful_reword_passes(self):
        result = verify_claim_against_evidence(
            "Engineered high-throughput REST APIs (2M req/day) in Python, containerized with Docker.",
            self._evidence_texts, overlap_threshold=0.35,
        )
        assert result.passed

    def test_fabricated_number_fails(self):
        """The exact regression case this module was built to catch: a
        single invented statistic dropped into an otherwise-grounded
        claim. Caught a real bug in the first draft of this check during
        implementation — the number regex missed magnitude-suffixed
        numbers (50M) entirely due to a \\b/letter boundary issue."""
        result = verify_claim_against_evidence(
            "Built REST APIs handling 50M requests per day with Python and Docker.",
            self._evidence_texts, overlap_threshold=0.35,
        )
        assert not result.passed
        assert "50M" in result.unsupported_facts

    def test_fabricated_named_entity_fails(self):
        result = verify_claim_against_evidence(
            "Built REST APIs at Google Cloud Platform using Python and Docker.",
            self._evidence_texts, overlap_threshold=0.35,
        )
        assert not result.passed
        assert result.unsupported_facts

    def test_wholesale_invention_fails(self):
        """Off-topic invention still fails — now caught by the fact check
        (its invented numbers '12'/'500' aren't in the evidence) rather
        than by the token-overlap floor."""
        result = verify_claim_against_evidence(
            "Led a team of 12 engineers at a Fortune 500 company managing cloud infrastructure.",
            self._evidence_texts, overlap_threshold=0.35,
        )
        assert not result.passed
        assert result.unsupported_facts

    def test_grounded_reword_with_low_overlap_passes(self):
        """The core 'verify claims, not tokens' behavior: a truthful
        rephrase whose only shared token is the grounded number passes,
        even though its token overlap is far below the old 0.35 floor."""
        result = verify_claim_against_evidence(
            "Achieved a 25% reduction in subscriber attrition.",
            ["Reduced customer churn 25% for the subscription product."],
            overlap_threshold=0.35,
        )
        assert result.passed

    def test_empty_claim_fails(self):
        result = verify_claim_against_evidence("", self._evidence_texts, overlap_threshold=0.35)
        assert not result.passed

    def test_empty_evidence_fails(self):
        result = verify_claim_against_evidence("Built REST APIs.", [], overlap_threshold=0.35)
        assert not result.passed

    def test_number_regex_catches_magnitude_suffixed_numbers(self):
        """Direct regression test for the boundary bug found during
        implementation: \\b\\d[\\d,]*\\.?\\d*%?\\b matches nothing at all
        on '50M' because there's no word boundary between a digit and an
        immediately-following letter."""
        result = verify_claim_against_evidence(
            "Managed a $50K budget.", ["Managed a $2K budget for the team offsite."],
            overlap_threshold=0.1,
        )
        assert not result.passed
        assert "$50K" in result.unsupported_facts

    def test_proper_noun_followed_by_capital_i_is_not_a_false_positive(self):
        """Direct regression test for a bug found during cover letter
        generation testing: 'At Acme Corp I built...' (no comma before
        'I') matched _PROPER_NOUN_RE as a single 3-word span ('Acme Corp
        I') since the capitalized first-person pronoun sits directly next
        to a real proper noun — the resulting 'fact' then failed
        verification even though 'Acme Corp' alone is genuinely grounded."""
        result = verify_claim_against_evidence(
            "At Acme Corp I built REST APIs using Python.",
            ["Acme Corp Software Engineer Built REST APIs using Python."],
            overlap_threshold=0.3,
        )
        assert result.passed

    def test_trailing_capital_i_alone_still_yields_no_fact_below_threshold(self):
        """Stripping a trailing 'I' from a 2-word span ('Acme I') must
        drop below the multi-word threshold and be excluded entirely,
        matching the existing sentence-start-trim precedent."""
        result = verify_claim_against_evidence(
            "Acme I worked hard.",
            ["Nothing relevant here at all whatsoever really."],
            overlap_threshold=0.0,
        )
        # No fact should be raised for "Acme" alone (dropped below the
        # 2-word threshold once "I" is stripped) — only the overlap
        # floor (deliberately set to 0.0 here) determines the outcome.
        assert result.unsupported_facts is None or "Acme I" not in (result.unsupported_facts or [])
