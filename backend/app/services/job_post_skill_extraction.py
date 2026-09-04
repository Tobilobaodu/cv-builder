"""M3 — LLM skill-extraction enrichment for job posts.

Composes with M1 (rules-based parser) and M2 (ESCO/O*NET taxonomy),
doesn't replace them. Only called when the rules-based+taxonomy parse
found few structured requirements (settings.job_post_llm_enrichment_min_requirements)
— this is exactly the prose-heavy-posting gap confirmed live during the
M1/M2 walkthrough (a real posting whose requirements were full sentences
scored 0.06 both before and after the taxonomy work, because neither a
keyword list nor a taxonomy lookup can distill prose into skill terms).

Never fabricates: every extracted phrase is verified against the
source job-post text using the same token-overlap check
tailored_cv_generation.py uses to verify generated CV claims against
cited evidence (app/extraction/evidence_binder.py) — an unverified
phrase is dropped, not merged in. Never blocks the job-post parse
pipeline: any failure (LLM call error, schema validation, all
extractions failing verification) results in an empty list, and the
rules-based result is used exactly as before.
"""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.extraction.evidence_binder import verify_claim_against_evidence
from app.prompts.job_post_prompts import (
    JOB_POST_SKILL_EXTRACTION_JSON_SCHEMA,
    JOB_POST_SKILL_EXTRACTION_PROMPT_VERSION,
    JOB_POST_SKILL_EXTRACTION_SYSTEM_PROMPT,
    JOB_POST_SKILL_EXTRACTION_TASK,
    build_user_payload,
)
from app.services.llm_client import (
    LlmCallError,
    LlmSchemaValidationError,
    generate_structured,
)

logger = get_logger(__name__)


def should_enrich(required_skills: list[str] | None, qualifications: list[str] | None) -> bool:
    """Whether the rules-based+taxonomy extraction needs LLM enrichment.
    Cost/latency-conscious by design — most well-structured postings
    never reach this. Two independent triggers, not just one:

    1. Too few items found (the original, obvious case).
    2. Enough items were found, but they're prose sentences rather than
       skill terms — checked directly against a real posting during
       development: 8 "qualifications" were extracted (well above the
       count floor) but every one was an 11-24 word sentence lifted
       whole from a "Who you are" paragraph, not a discrete skill claim.
       A count-only gate would have silently skipped enrichment on
       exactly the posting this feature exists to help.
    """
    if not settings.job_post_llm_enrichment_enabled:
        return False

    items = (required_skills or []) + (qualifications or [])
    if len(items) < settings.job_post_llm_enrichment_min_requirements:
        return True

    avg_words = sum(len(item.split()) for item in items) / len(items)
    return avg_words > settings.job_post_llm_enrichment_prose_word_threshold


def extract_skills_via_llm(
    raw_text: str,
    *,
    llm_client_override=None,
) -> list[str]:
    """Call the LLM once, verify every returned phrase against the real
    source text, and return only what passes. Never raises — errors are
    logged and result in an empty list, since this is purely additive
    enrichment on top of an already-complete rules-based result."""
    if not raw_text or not raw_text.strip():
        return []

    try:
        result = generate_structured(
            system_prompt=JOB_POST_SKILL_EXTRACTION_SYSTEM_PROMPT,
            user_payload=build_user_payload(raw_text),
            json_schema=JOB_POST_SKILL_EXTRACTION_JSON_SCHEMA,
            schema_name=JOB_POST_SKILL_EXTRACTION_TASK,
            max_tokens=600,  # short phrases only
            client=llm_client_override,
            prompt_version=JOB_POST_SKILL_EXTRACTION_PROMPT_VERSION,
        )
    except (LlmCallError, LlmSchemaValidationError) as e:
        logger.warning("job_post_skill_extraction_call_failed", error=str(e))
        return []

    candidates = result.data.get("skills")
    if not isinstance(candidates, list):
        return []

    verified: list[str] = []
    rejected_count = 0
    for phrase in candidates:
        if not isinstance(phrase, str) or not phrase.strip():
            continue
        verification = verify_claim_against_evidence(
            phrase,
            [raw_text],
            settings.job_post_llm_evidence_overlap_threshold,
        )
        if verification.passed:
            verified.append(phrase.strip())
        else:
            rejected_count += 1

    logger.info(
        "job_post_skill_extraction_complete",
        extracted=len(candidates),
        verified=len(verified),
        rejected=rejected_count,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
    )
    return verified
