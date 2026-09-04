"""LLM-based match analysis between a CV and a job post — one call.

Replaces match_engine.py's rules-based matching as the engine behind
POST /matches now that the structured CV profile it depended on
(cv_profile_versions.structured_payload from the decommissioned cv_parse
step) is no longer produced for real CVs — see
app/workers/worker_jobs.py's top-of-file comment. match_engine.py itself
is untouched (still directly unit-tested); this is a new, separate
engine, not a rewrite of that one.

Mirrors app/services/resume_rewrite.py's shape exactly: an injectable
`client` for tests, generate_structured() as the only LLM call seam, and
LLM_GENERATION_COUNTER/LLM_TOKENS_COUNTER instrumentation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.logging import get_logger
from app.core.metrics import LLM_GENERATION_COUNTER, LLM_TOKENS_COUNTER
from app.extraction.match_engine import (
    CONTRADICTORY,
    PARTIALLY_SUPPORTED,
    SUPPORTED,
    UNCLEAR,
    UNSUPPORTED,
    EvidenceItem,
)
from app.prompts import match_analysis_prompts as prompts
from app.services.llm_client import (
    LlmCallError,
    LlmSchemaValidationError,
    generate_structured,
)

logger = get_logger(__name__)

_VALID_SUPPORT_LEVELS = frozenset(
    {SUPPORTED, PARTIALLY_SUPPORTED, UNSUPPORTED, CONTRADICTORY, UNCLEAR}
)


@dataclass
class MatchAnalysisResult:
    """Mirrors match_engine.MatchResult's field names exactly, plus the
    ats_issues/formatting_issues/tips fields the LLM engine additionally
    produces."""
    score: float
    supported_count: int
    partial_count: int
    unsupported_count: int
    contradictory_count: int = 0
    unclear_count: int = 0
    total_requirements: int = 0
    summary_analysis: str = ""
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    ats_issues: list[dict] = field(default_factory=list)
    formatting_issues: list[dict] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_version: str = prompts.MATCH_ANALYSIS_PROMPT_VERSION


class MatchAnalysisError(RuntimeError):
    """The match analysis could not be produced. Message is caller-safe."""


def _parse_evidence_item(raw: dict) -> EvidenceItem | None:
    support_level = raw.get("supportLevel")
    if support_level not in _VALID_SUPPORT_LEVELS:
        # A model that violates the schema's enum shouldn't be possible
        # under strict mode, but this is the one field the six derived
        # count fields below depend on being exactly right — defensive,
        # not decorative. Anything unrecognised is dropped rather than
        # silently miscounted.
        logger.warning("match_analysis_unknown_support_level", value=support_level)
        return None
    requirement_type = raw.get("requirementType")
    if requirement_type not in ("required", "preferred"):
        requirement_type = "required"
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return EvidenceItem(
        requirement_text=raw.get("requirementText") or "",
        requirement_type=requirement_type,
        support_level=support_level,
        confidence=max(0.0, min(1.0, confidence)),
        source_references=[r for r in (raw.get("sourceReferences") or []) if r],
        suggestion=raw.get("suggestion") or None,
        warning=raw.get("warning") or None,
    )


def run_match_llm(
    cv_text: str,
    job_post_text: str,
    job_post_profile: dict | None = None,
    *,
    client=None,
) -> MatchAnalysisResult:
    """Run the match analysis. Raises MatchAnalysisError on any LLM failure.

    The six count fields (supported_count, partial_count, ...) are always
    derived here from evidence_items by counting support_level values —
    never taken from whatever the model may have self-reported, so a
    malformed or self-inconsistent model response can never desynchronise
    the counts from the evidence list a caller actually sees.
    """
    if not cv_text or not cv_text.strip():
        raise MatchAnalysisError("No CV text to work from.")
    if not job_post_text or not job_post_text.strip():
        raise MatchAnalysisError("No job post text to work from.")

    payload = prompts.build_user_payload(
        cv_text=cv_text, job_post_text=job_post_text, job_post_profile=job_post_profile,
    )

    try:
        result = generate_structured(
            system_prompt=prompts.MATCH_ANALYSIS_SYSTEM_PROMPT,
            user_payload=payload,
            json_schema=prompts.MATCH_ANALYSIS_JSON_SCHEMA,
            schema_name=prompts.MATCH_ANALYSIS_TASK,
            max_tokens=2000,  # evidence items scale with requirement count
            client=client,
            prompt_version=prompts.MATCH_ANALYSIS_PROMPT_VERSION,
        )
    except (LlmCallError, LlmSchemaValidationError) as e:
        LLM_GENERATION_COUNTER.labels(
            generation_task=prompts.MATCH_ANALYSIS_TASK, outcome="failed",
        ).inc()
        logger.error("match_analysis_failed", error=str(e))
        raise MatchAnalysisError(
            "We couldn't finish the match analysis. Please try again in a moment."
        ) from e

    data = result.data
    LLM_GENERATION_COUNTER.labels(
        generation_task=prompts.MATCH_ANALYSIS_TASK, outcome="succeeded",
    ).inc()
    for token_type, count in (
        ("prompt", result.prompt_tokens),
        ("completion", result.completion_tokens),
    ):
        if count:
            LLM_TOKENS_COUNTER.labels(
                generation_task=prompts.MATCH_ANALYSIS_TASK, token_type=token_type,
            ).inc(count)

    evidence_items = [
        item for item in (
            _parse_evidence_item(raw) for raw in (data.get("evidenceItems") or [])
        )
        if item is not None
    ]

    # ── Derive counts from evidence_items in code — never trust the ────
    # model's self-reported score/counts to agree with its own list.
    supported = sum(1 for e in evidence_items if e.support_level == SUPPORTED)
    partial = sum(1 for e in evidence_items if e.support_level == PARTIALLY_SUPPORTED)
    unsupported = sum(1 for e in evidence_items if e.support_level == UNSUPPORTED)
    contradictory = sum(1 for e in evidence_items if e.support_level == CONTRADICTORY)
    unclear = sum(1 for e in evidence_items if e.support_level == UNCLEAR)
    total = len(evidence_items)

    try:
        score = float(data.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    score = round(max(0.0, min(1.0, score)), 2)

    logger.info(
        "match_analysis_complete",
        model=result.model,
        score=score,
        supported=supported,
        partial=partial,
        unsupported=unsupported,
        contradictory=contradictory,
        unclear=unclear,
        total=total,
    )

    return MatchAnalysisResult(
        score=score,
        supported_count=supported,
        partial_count=partial,
        unsupported_count=unsupported,
        contradictory_count=contradictory,
        unclear_count=unclear,
        total_requirements=total,
        summary_analysis=data.get("summaryAnalysis") or "",
        evidence_items=evidence_items,
        ats_issues=list(data.get("atsIssues") or []),
        formatting_issues=list(data.get("formattingIssues") or []),
        tips=list(data.get("tips") or []),
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
