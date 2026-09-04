"""Orchestrates cover letter draft generation (Sprint 4).

Primary path: one real LLM call for the whole letter body (not a
per-section loop like tailored-CV generation) — a cover letter is meant
to read as one coherent narrative voice, and independent per-paragraph
calls risk a disjointed, repetitive letter since each call can't see
what the others wrote. Falls back to the deterministic template
(cover_letter.assemble_draft()) if verification fails after retries, or
if settings.cover_letter_llm_generation_enabled is False (an explicit
kill switch) — implementing the roadmap's own words: "assemble_draft()
becomes the fallback/template layer only if generation fails or is
explicitly disabled."

No DB session handling here, same as tailored_cv_generation.py —
worker_jobs.py::process_cover_letter_generate loads rows and persists
the result; this module is pure orchestration, testable with a fake LLM
client and no live DB.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger
from app.extraction import evidence_binder
from app.prompts import cover_letter_prompts as prompts
from app.services import cover_letter
from app.services import generation_core

logger = get_logger(__name__)


@dataclass
class CoverLetterGenerationResult:
    body_text: str
    evidence_references: list[str]
    prompt_version: str | None
    model_id: str | None
    source: str  # "llm" | "fallback"


def build_evidence_pool(
    *,
    match_evidence_items: list,
    experience_items: list,
    education_items: list,
    skill_items: list,
    certification_items: list,
    project_items: list,
    questions_by_id: dict,
    answers: list,
) -> list[evidence_binder.EvidenceCandidate]:
    """CV-side candidates + the candidate's own Q&A answers.

    When a match exists, CV candidates are relevance-filtered via
    bind_evidence_pool (job-targeted, tighter). When no match exists (a
    match is optional on POST /cover-letters/start), the raw unfiltered
    pool is used instead — direct precedent: tailored_cv_generation.py's
    _generate_education_section() ("a real degree shouldn't disappear
    just because this posting didn't ask for one"); a candidate's real
    work history shouldn't disappear from their cover letter's evidence
    pool just because they started the workflow without running a match.

    Answers are always appended unconditionally, never relevance-
    filtered — there are only ~5-8 per workflow, each purpose-written by
    the user for this exact application.
    """
    cv_candidates = evidence_binder.build_candidate_pool(
        experience_items, education_items, skill_items,
        certification_items=certification_items, project_items=project_items,
    )
    cv_pool = (
        evidence_binder.bind_evidence_pool(match_evidence_items, cv_candidates)
        if match_evidence_items
        else cv_candidates
    )
    answer_pool = evidence_binder.build_answer_candidates(questions_by_id, answers)
    return cv_pool + answer_pool


def generate_draft(
    *,
    evidence_pool: list[evidence_binder.EvidenceCandidate],
    job_requirements: list[str],
    job_title: str,
    employer_name: str | None,
    cv_name: str | None,
    tone: str | None,
    answers_by_step: dict[int, list[tuple[str, str]]],
    experience_items: list,
    project_items: list,
    skill_items: list,
    llm_client_override=None,
) -> CoverLetterGenerationResult:
    if settings.cover_letter_llm_generation_enabled:
        outcome = generation_core.GenerationOutcome()
        user_payload = prompts.build_cover_letter_user_payload(
            evidence_pool_text=prompts.format_evidence_pool(evidence_pool),
            job_requirements=job_requirements,
            job_title=job_title,
            employer_name=employer_name or "the company",
            candidate_name=cv_name,
            tone=tone,
        )
        # Structural facts a well-formed letter is expected to state
        # (role, employer, candidate name, the generic salutation) but
        # which aren't part of the citable evidence pool — without this,
        # the hard-fact check would reject every real letter for stating
        # its own job title/employer/candidate name, confirmed live
        # during implementation (see generate_and_verify_section's
        # extra_verification_context docstring).
        structural_context = " ".join(
            p for p in [job_title, employer_name, cv_name, "Hiring Manager"] if p
        )
        section = generation_core.generate_and_verify_section(
            section_type="cover_letter_body",
            system_prompt=prompts.COVER_LETTER_SYSTEM_PROMPT,
            generation_task=prompts.COVER_LETTER_GENERATION_TASK,
            prompt_version=prompts.COVER_LETTER_PROMPT_VERSION,
            schema=prompts.COVER_LETTER_JSON_SCHEMA,
            schema_name="cover_letter_body",
            candidates=evidence_pool,
            user_payload=user_payload,
            order_index=0,
            outcome=outcome,
            overlap_threshold=settings.cover_letter_evidence_overlap_threshold,
            max_attempts=settings.cover_letter_max_generation_retries,
            extra_verification_context=structural_context,
            llm_client_override=llm_client_override,
        )
        if section:
            return CoverLetterGenerationResult(
                body_text=section.content_text,
                evidence_references=section.evidence_references,
                prompt_version=section.prompt_version,
                model_id=section.model_id,
                source="llm",
            )
        logger.warning(
            "cover_letter_llm_generation_failed_using_fallback",
            issues=outcome.issues,
        )
    else:
        logger.info("cover_letter_llm_generation_disabled_using_fallback")

    assembled = cover_letter.assemble_draft(
        cv_name=cv_name,
        employer_name=employer_name,
        job_title=job_title,
        tone=tone,
        answers_by_step=answers_by_step,
        experience_items=experience_items,
        project_items=project_items,
        skill_items=skill_items,
        job_requirements=job_requirements,
    )
    return CoverLetterGenerationResult(
        body_text=assembled.body_text,
        evidence_references=assembled.evidence_references,
        prompt_version="cover_letter_fallback_v1",
        model_id="rules-based",
        source="fallback",
    )
