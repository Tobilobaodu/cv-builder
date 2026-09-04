"""LLM-based, job-agnostic CV analysis — one call per CV.

Replaces the decommissioned Docling/Textract/merge/cv_parse pipeline's
role as the source of a per-CV quality signal (see
app/workers/worker_jobs.py's top-of-file comment). Sends the CV's
already-extracted raw text to the model and gets back a resume-quality
score, ATS/formatting issue checklists, improvement tips, and a minimal
basics/skills extraction.

The basics/skills extraction is not a resurrection of the old structured
parser — it's deliberately minimal, existing only so
worker_jobs.py::process_cv_analyze can write a CvProfileVersion/
CvProfile row that satisfies MatchRun/CoverLetterWorkflow's NOT NULL FKs.

Mirrors app/services/resume_rewrite.py's shape exactly: an injectable
`client` for tests, generate_structured() as the only LLM call seam, and
LLM_GENERATION_COUNTER/LLM_TOKENS_COUNTER instrumentation.
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
from app.core.metrics_push import push_worker_metrics
from app.prompts import cv_analysis_prompts as prompts
from app.services.llm_client import (
    LlmCallError,
    LlmSchemaValidationError,
    generate_structured,
)

logger = get_logger(__name__)


@dataclass
class CvAnalysisResult:
    overall_score: float
    skillset_score: float
    formatting_score: float
    ats_issues: list[dict] = field(default_factory=list)
    formatting_issues: list[dict] = field(default_factory=list)
    tips: list[str] = field(default_factory=list)
    basics: dict = field(default_factory=dict)
    skills: list[str] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_version: str = prompts.CV_ANALYSIS_PROMPT_VERSION


class CvAnalysisError(RuntimeError):
    """The analysis could not be produced. Message is caller-safe."""


def _clamp_score(value) -> float:
    try:
        score = float(value or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(100.0, score))


def analyze_cv(cv_text: str, *, client=None) -> CvAnalysisResult:
    """Run the analysis. Raises CvAnalysisError on any LLM failure.

    No retry-with-correction loop, same reasoning as resume_rewrite.py:
    there's no evidence pool here either, so a retry has nothing more to
    go on than the first attempt did. generate_structured already
    retries transient API errors internally.
    """
    if not cv_text or not cv_text.strip():
        raise CvAnalysisError("No CV text to analyse.")

    payload = prompts.build_user_payload(cv_text=cv_text)

    try:
        result = generate_structured(
            system_prompt=prompts.CV_ANALYSIS_SYSTEM_PROMPT,
            user_payload=payload,
            json_schema=prompts.CV_ANALYSIS_JSON_SCHEMA,
            schema_name=prompts.CV_ANALYSIS_TASK,
            max_tokens=1200,  # scores plus issue lists
            client=client,
            prompt_version=prompts.CV_ANALYSIS_PROMPT_VERSION,
        )
    except (LlmCallError, LlmSchemaValidationError) as e:
        LLM_GENERATION_COUNTER.labels(
            generation_task=prompts.CV_ANALYSIS_TASK, outcome="failed",
        ).inc()
        logger.error("cv_analysis_failed", error=str(e))
        raise CvAnalysisError(
            "We couldn't finish analysing this CV. Please try again in a moment."
        ) from e

    data = result.data
    LLM_GENERATION_COUNTER.labels(
        generation_task=prompts.CV_ANALYSIS_TASK, outcome="succeeded",
    ).inc()
    for token_type, count in (
        ("prompt", result.prompt_tokens),
        ("completion", result.completion_tokens),
    ):
        if count:
            LLM_TOKENS_COUNTER.labels(
                generation_task=prompts.CV_ANALYSIS_TASK, token_type=token_type,
            ).inc(count)

    basics = dict(data.get("basics") or {})
    skills = [s for s in (data.get("skills") or []) if s]
    overall_score = _clamp_score(data.get("overallScore"))

    # O2: length-bias check — this runs in worker_cv_analyze, so it needs
    # the same Pushgateway path as EVIDENCE_VERIFICATION_COUNTER above.
    ANALYSIS_SCORE_BY_LENGTH.labels(length_bucket=length_bucket(len(cv_text))).observe(overall_score)
    push_worker_metrics("worker_cv_analyze")

    logger.info(
        "cv_analysis_complete",
        model=result.model,
        overall_score=data.get("overallScore"),
        skillset_score=data.get("skillsetScore"),
        formatting_score=data.get("formattingScore"),
        skills_extracted=len(skills),
    )

    return CvAnalysisResult(
        overall_score=overall_score,
        skillset_score=_clamp_score(data.get("skillsetScore")),
        formatting_score=_clamp_score(data.get("formattingScore")),
        ats_issues=list(data.get("atsIssues") or []),
        formatting_issues=list(data.get("formattingIssues") or []),
        tips=list(data.get("tips") or []),
        basics=basics,
        skills=skills,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
