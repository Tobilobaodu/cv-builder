"""Fast match analysis — the "small fields" half of the single-call resume
rewrite this was split out of (see app/services/resume_rewrite.py).

Deliberately synchronous and stateless, same reasoning as resume_rewrite.py:
one LLM call, no Celery job, no DB row, no polling — the caller gets the
finished result on the response. Small output (~350-450 tokens) is what
makes that viable here where it isn't for the markdown-generating half.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.core.metrics import (
    ANALYSIS_SCORE_BY_LENGTH,
    LLM_GENERATION_COUNTER,
    LLM_TOKENS_COUNTER,
    length_bucket,
)
from app.extraction.skills_index import literal_coverage
from app.prompts import resume_analysis_prompts as prompts
from app.services.llm_client import (
    LlmCallError,
    LlmSchemaValidationError,
    generate_structured,
)

logger = get_logger(__name__)


@dataclass
class ResumeAnalysisResult:
    match_notes: list[str] = field(default_factory=list)
    information_needed: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    prompt_version: str = prompts.RESUME_ANALYSIS_PROMPT_VERSION
    # Real usage, for the caller's own spend accounting (C1) — not part of
    # the API response shape (resume_rewrites.py's MatchAnalysisResponse
    # doesn't expose these), read directly off this dataclass instead.
    prompt_tokens: int = 0
    completion_tokens: int = 0


class ResumeAnalysisError(RuntimeError):
    """The analysis could not be produced. Message is caller-safe."""


# Kept identical to resume_rewrite.py's constants of the same name — both
# enforce the same cross-occupation cap and label bands in code rather than
# trusting the model's own arithmetic (see that file's docstring: asked for
# the cap in words, the model returned 68 for a product-design CV against
# an HR role). Small enough, and specific enough to each module's error
# handling around it, that sharing a helper module for these four lines
# would cost more than it saves.
_CROSS_OCCUPATION_SCORE_CAP = 40.0
_STRONG_MATCH_FROM = 75.0
_GOOD_MATCH_FROM = 50.0


def _label_for(score: float) -> str:
    if score >= _STRONG_MATCH_FROM:
        return "Strong match"
    if score >= _GOOD_MATCH_FROM:
        return "Good match"
    return "Needs work"


def analyze_resume(
    *,
    cv_text: str,
    job_post_text: str,
    target_title: str | None = None,
    llm_client_override=None,
) -> ResumeAnalysisResult:
    """Run the analysis. Raises ResumeAnalysisError on any LLM failure.

    Tight timeout, one retry: this call is on the critical path the user
    is actively waiting on (see 30-second-target sequencing), unlike
    generation which is streamed/off-path once split from this call.
    """
    if not cv_text or not cv_text.strip():
        raise ResumeAnalysisError("No CV text to work from.")
    if not job_post_text or not job_post_text.strip():
        raise ResumeAnalysisError("No job post text to work from.")

    payload = prompts.build_user_payload(
        cv_text=cv_text, job_post_text=job_post_text, target_title=target_title,
    )

    try:
        result = generate_structured(
            system_prompt=prompts.RESUME_ANALYSIS_SYSTEM_PROMPT,
            user_payload=payload,
            json_schema=prompts.RESUME_ANALYSIS_JSON_SCHEMA,
            schema_name=prompts.RESUME_ANALYSIS_TASK,
            max_tokens=800,
            timeout=15,
            max_api_retries=1,
            client=llm_client_override,
            prompt_version=prompts.RESUME_ANALYSIS_PROMPT_VERSION,
        )
    except (LlmCallError, LlmSchemaValidationError) as e:
        LLM_GENERATION_COUNTER.labels(
            generation_task=prompts.RESUME_ANALYSIS_TASK, outcome="failed",
        ).inc()
        logger.error("resume_analysis_failed", error=str(e))
        raise ResumeAnalysisError(
            "We couldn't finish the analysis. Please try again in a moment."
        ) from e

    data = result.data
    LLM_GENERATION_COUNTER.labels(
        generation_task=prompts.RESUME_ANALYSIS_TASK, outcome="succeeded",
    ).inc()
    for token_type, count in (
        ("prompt", result.prompt_tokens),
        ("completion", result.completion_tokens),
    ):
        if count:
            LLM_TOKENS_COUNTER.labels(
                generation_task=prompts.RESUME_ANALYSIS_TASK, token_type=token_type,
            ).inc(count)

    stats = dict(data.get("stats") or {})
    match_notes = list(data.get("matchNotes") or [])
    information_needed = list(data.get("informationNeeded") or [])

    # ── Enforce the cross-occupation cap in code ─────────────────────
    raw_score = float(stats.get("atsScore") or 0.0)
    score = max(0.0, min(100.0, raw_score))
    same_occupation = bool(stats.get("sameOccupation", True))
    if not same_occupation:
        score = min(score, _CROSS_OCCUPATION_SCORE_CAP)
    stats["atsScore"] = score
    stats["matchLabel"] = _label_for(score)
    ANALYSIS_SCORE_BY_LENGTH.labels(length_bucket=length_bucket(len(cv_text))).observe(score)

    if not same_occupation:
        cv_occ = (stats.get("cvOccupation") or "").strip()
        job_occ = (stats.get("jobOccupation") or "").strip()
        if cv_occ and job_occ:
            note = (
                f"Different profession: this CV evidences {cv_occ}, "
                f"the role is {job_occ}. Scored as a career change, so the "
                "score is capped regardless of overlapping vocabulary."
            )
            if note not in match_notes:
                match_notes.insert(0, note)

    # ── Literal keyword coverage alongside the semantic score (Q1) ────
    # Free — deterministic, no model call — and answers a different
    # question than atsScore does: not "is this candidate a good fit"
    # but "would a strict, synonym-blind ATS keyword filter see these
    # terms in the CV at all".
    stats["literalCoverage"] = literal_coverage(
        cv_text, stats.get("priorityKeywords") or []
    )

    logger.info(
        "resume_analysis_complete",
        model=result.model,
        match_notes=len(match_notes),
        same_occupation=same_occupation,
        score_raw=raw_score,
        score_final=score,
    )

    return ResumeAnalysisResult(
        match_notes=match_notes,
        information_needed=information_needed,
        stats=stats,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
