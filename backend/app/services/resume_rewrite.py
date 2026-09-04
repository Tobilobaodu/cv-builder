"""Single-call resume rewrite — generation half only (see
resume_analysis.py for the score/occupation/matched-skills half this was
split from, jbs-solution-sheet.md S1).

Deliberately synchronous-per-chunk and stateless: no Celery job, no DB
row. Nothing here is persisted, so there is no migration and no polling —
the caller streams the finished result as it's produced. That mirrors the
flow this replaces conceptually (Example's /resume/rewrite) and is only
viable because the whole rewrite is a single call rather than the eight
per-section calls tailored_cv_generation.py makes.

If this ever needs history, provenance, or regeneration-from-draft, it
needs a table and a worker — do not bolt persistence onto the request
path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import LLM_GENERATION_COUNTER, LLM_TOKENS_COUNTER
from app.prompts import resume_rewrite_prompts as prompts
from app.services.llm_client import (
    LlmCallError,
    LlmSchemaValidationError,
    generate_structured,
    stream_text,
)

logger = get_logger(__name__)


@dataclass
class ResumeRewriteResult:
    tailored_resume_markdown: str
    information_needed: list[str] = field(default_factory=list)
    prompt_version: str = prompts.RESUME_REWRITE_PROMPT_VERSION
    # Server-internal token accounting for the caller's cost tracking
    # ({"prompt_tokens", "completion_tokens"}); None when not requested.
    # Additive — existing consumers are unaffected.
    usage: dict | None = None
    # v5 additions (sync path only — see RESUME_REWRITE_JSON_SCHEMA):
    # rewrittenExperience/suggestedAdditions from the model, already passed
    # through the same truthfulness safety nets as the markdown. Empty on
    # the streamed path, which doesn't request these fields. Additive —
    # existing consumers reading only tailored_resume_markdown are
    # unaffected.
    rewritten_experience: list[dict] = field(default_factory=list)
    suggested_additions: list[str] = field(default_factory=list)


@dataclass
class RewriteStreamEvent:
    """One item from stream_rewrite_resume().

    type "delta": `text` is the next chunk to append as it arrives.
    type "corrected": the code-side safety nets (below) removed something
      from what was already streamed — `text` is the full, corrected
      markdown; the caller must replace whatever it has rendered so far
      with this, not append it. Rare: only fires when a location or a
      lifted job-post requirement the CV doesn't support slipped through.
    type "done": stream finished clean, nothing needed correcting.
    Either terminal event carries the final `information_needed`.
    """

    type: str
    text: str = ""
    information_needed: list[str] = field(default_factory=list)


class ResumeRewriteError(RuntimeError):
    """The rewrite could not be produced. Message is caller-safe."""


# A standalone line that is just a place: "Dublin, Ireland",
# "London, United Kingdom". Deliberately narrow — no digits, no bullet or
# heading marker, at most three comma-separated parts, each starting
# capitalised — so it cannot match a skills line or a role heading.
_LOCATION_ONLY_LINE = re.compile(
    r"^[A-Z][A-Za-z.'’-]*(?:[ ][A-Z][A-Za-z.'’-]*)*"
    r"(?:,[ ]*[A-Z][A-Za-z.'’-]*(?:[ ][A-Z][A-Za-z.'’-]*)*){1,2}$"
)
_MAX_LOCATION_LINE_CHARS = 48


# A line copied this closely from the job post is the job post's wording,
# not the candidate's. A genuinely tailored bullet carries the candidate's
# own specifics — employers, numbers, systems — which drag containment down.
_LIFTED_FROM_JOB_MIN = 0.8
# ...and if the CV supports it even loosely, it stays. Stripping a real
# claim is worse than leaving a borrowed phrasing.
_SUPPORTED_BY_CV_MIN = 0.5

# A paraphrase escapes the pair above: told not to copy the requirement,
# the model returned "Willingness to travel as required throughout the
# region" — 0.75 lifted, just under the bar. But its CV support was 0.00,
# and a line sharing not one content word with a 7,000-character CV is not
# drawn from that CV whatever its phrasing. Lower lift bar, absolute
# support bar.
_PARAPHRASE_LIFTED_MIN = 0.6
# Below this many content words a line is too generic to judge either way
# ("Workday HRIS", "GDPR compliance") — left alone.
_MIN_JUDGEABLE_WORDS = 4

_WORD = re.compile(r"[a-z0-9%+]+")
_STOPWORDS = frozenset(
    """a an the and or of to in on for with within across at by as is are be
    including include includes strong experience experienced ability able
    knowledge working work works while their your our its this that these
    those from throughout approximately""".split()
)


# Crude suffix stripping, longest suffix first. Without it the job post's
# "Willingness to travel" and the CV line's "Willing to travel" count as
# different words and the copied claim scores 0.75 — just under the bar.
# A stem is only taken when what remains is still a real-looking word, so
# "region" never becomes "reg".
_SUFFIXES = ("ingly", "ional", "ments", "ness", "ions", "ing", "ment", "edly",
             "ies", "ion", "ed", "es", "ly", "s")
_MIN_STEM_CHARS = 4


def _stem(word: str) -> str:
    # Applied repeatedly: "willingness" -> "willing" -> "will" has to reach
    # the same stem as "willing" -> "will", or the pair still misses.
    for _ in range(3):
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM_CHARS:
                word = word[: -len(suffix)]
                break
        else:
            break
    return word


def _content_words(text: str) -> set[str]:
    return {
        _stem(w) for w in _WORD.findall(text.casefold())
        if w not in _STOPWORDS and len(w) > 1
    }


def _containment(needle: set[str], haystack: set[str]) -> float:
    """Fraction of `needle` present in `haystack`."""
    if not needle:
        return 0.0
    return len(needle & haystack) / len(needle)


def _is_lifted_claim(text: str, job_lines: list[set[str]], cv_words: set[str]) -> bool:
    """Shared judgment behind _strip_lifted_requirements and
    _filter_lifted_texts: is `text` copied from the job post's wording
    without the CV backing it up? Takes bare text — the caller decides
    whether that text came from a markdown line or a structured field."""
    words = _content_words(text)
    if len(words) < _MIN_JUDGEABLE_WORDS:
        return False
    lifted = max((_containment(words, jl) for jl in job_lines), default=0.0)
    supported = _containment(words, cv_words)
    near_verbatim = lifted >= _LIFTED_FROM_JOB_MIN and supported < _SUPPORTED_BY_CV_MIN
    unsupported_paraphrase = lifted >= _PARAPHRASE_LIFTED_MIN and supported == 0.0
    return near_verbatim or unsupported_paraphrase


def _strip_lifted_requirements(
    markdown: str, cv_text: str, job_post_text: str
) -> tuple[str, list[str]]:
    """Remove claims copied out of the job post that the CV never supports.

    Observed live: a job post listing "Willingness to travel throughout the
    region (approximately 10%)" produced an "ADDITIONAL INFORMATION"
    section on the CV reading "Willing to travel throughout the region
    (approximately 10%)" — for a candidate whose CV says nothing about
    travel. The prompt already forbids claiming an unevidenced requirement,
    but its rules are written about experience and skills; a statement of
    willingness, work authorisation or availability is a personal
    declaration only the candidate can make, and slipped past them.

    Generic by design: it does not know what travel is. It asks whether a
    line is the job post's words rather than the candidate's, and whether
    the CV backs it up.
    """
    job_lines = [
        _content_words(line)
        for line in job_post_text.splitlines()
        if line.strip()
    ]
    cv_words = _content_words(cv_text)
    if not job_lines:
        return markdown, []

    kept: list[str] = []
    removed: list[str] = []

    for line in markdown.splitlines():
        stripped = line.strip()
        body = re.sub(r"^[-*+]\s+|^#{1,6}\s+", "", stripped)

        if not stripped.startswith("#") and _is_lifted_claim(body, job_lines, cv_words):
            removed.append(body)
            continue

        kept.append(line)

    return _drop_empty_sections("\n".join(kept)), removed


def _filter_lifted_texts(
    texts: list[str], cv_text: str, job_post_text: str
) -> tuple[list[str], list[str]]:
    """_strip_lifted_requirements for a flat list of free-text strings
    (rewrittenExperience bullets, suggestedAdditions) instead of markdown
    lines — no heading/bullet-marker stripping needed, same judgment."""
    job_lines = [
        _content_words(line)
        for line in job_post_text.splitlines()
        if line.strip()
    ]
    cv_words = _content_words(cv_text)
    if not job_lines:
        return list(texts), []

    kept: list[str] = []
    removed: list[str] = []
    for text in texts:
        if _is_lifted_claim(text, job_lines, cv_words):
            removed.append(text)
            continue
        kept.append(text)
    return kept, removed


def _drop_empty_sections(markdown: str) -> str:
    """Remove headings left with no content under them.

    Stripping the only bullet beneath "## Additional Information" would
    otherwise leave the heading stranded, which reads as a formatting bug.
    """
    lines = markdown.splitlines()
    keep = [True] * len(lines)

    for i, line in enumerate(lines):
        if not line.strip().startswith("#"):
            continue
        level = len(line) - len(line.lstrip("#"))
        has_content = False
        for j in range(i + 1, len(lines)):
            nxt = lines[j].strip()
            if not nxt:
                continue
            if nxt.startswith("#"):
                next_level = len(lines[j]) - len(lines[j].lstrip("#"))
                if next_level <= level:
                    break
                continue
            has_content = True
            break
        if not has_content:
            keep[i] = False

    return "\n".join(line for line, k in zip(lines, keep) if k)


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold()


def _is_invented_location(text: str, haystack: str) -> bool:
    """Shared judgment behind _strip_invented_locations and
    _filter_invented_locations: is `text`, in isolation, nothing but a
    place the CV never stated? `haystack` is the already-normalised CV
    text. Only whole strings that are nothing but a place match, so a
    location genuinely present in the CV, or one embedded in a sentence,
    is left alone."""
    candidate = text.strip().rstrip("|").strip()
    if not (
        candidate
        and len(candidate) <= _MAX_LOCATION_LINE_CHARS
        and _LOCATION_ONLY_LINE.match(candidate)
        and _normalise(candidate) not in haystack
    ):
        return False
    # Each comma-separated part must also be absent; "London" alone
    # appearing in the CV is enough to treat the line as supported.
    parts = [p.strip() for p in candidate.split(",") if p.strip()]
    return all(_normalise(p) not in haystack for p in parts)


def _strip_invented_locations(markdown: str, cv_text: str) -> tuple[str, list[str]]:
    """Remove location-only lines the source CV never stated.

    The prompt forbids inventing a location, and still produced
    "Dublin, Ireland" (the job's city) and "London, United Kingdom"
    against a CV containing neither. A location is a fact about the
    candidate, so a line the CV cannot support is deleted rather than
    shown; the caller reports what was removed instead.

    Only whole lines that are nothing but a place are touched, so a
    location genuinely present in the CV, or one embedded in a sentence,
    is left alone.
    """
    haystack = _normalise(cv_text)
    kept: list[str] = []
    removed: list[str] = []

    for line in markdown.splitlines():
        if _is_invented_location(line, haystack):
            removed.append(line.strip().rstrip("|").strip())
            continue
        kept.append(line)

    return "\n".join(kept), removed


def _filter_invented_locations(
    texts: list[str], cv_text: str
) -> tuple[list[str], list[str]]:
    """_strip_invented_locations for a flat list of free-text strings
    instead of markdown lines — same judgment, no line-splitting."""
    haystack = _normalise(cv_text)
    kept: list[str] = []
    removed: list[str] = []
    for text in texts:
        if _is_invented_location(text, haystack):
            removed.append(text.strip().rstrip("|").strip())
            continue
        kept.append(text)
    return kept, removed


# Deterministic, zero-LLM-cost backstop for the "Sounding human, not
# machine-written" prompt rules above — same defense-in-depth idiom as
# _strip_lifted_requirements/_strip_invented_locations (ask the model
# nicely, then enforce mechanically). Sourced from the same two places as
# the prompt section: the orphaned root tailored_cv_prompts.py's
# ANTI_AI_TELL_RULES "use the plain word instead" table, and
# clearspeaking.skill's lexical-flag taxonomy. Deliberately narrow: only
# single-word/short-phrase substitutions with an unambiguous, grammar-safe
# replacement. Rule-of-Three and "-ing" tail removal are NOT here — both
# require prose judgment a mechanical regex can't safely make (unlike
# deleting an unsupported line outright, rewriting a sentence's shape
# risks producing something ungrammatical), so those stay prompt-only.
_BUZZWORD_REPLACEMENTS: dict[str, str] = {
    "proven track record": "track record",
    "proven success": "success",
    "results-driven": "results-focused",
    "cutting-edge": "advanced",
    "state-of-the-art": "advanced",
    "alignment with": "match with",
    "align with": "match",
    "served as": "was",
    "serves as": "is",
    "stands as": "is",
    "features a": "has a",
    "boasts": "has",
    "leveraging": "using",
    "leverage": "use",
    "fostering": "building",
    "foster": "build",
    "showcasing": "highlighting",
    "showcase": "highlight",
    "underscoring": "highlighting",
    "underscore": "highlight",
    "enhancing": "improving",
    "enhancement": "improvement",
    "enhance": "improve",
    "bolstering": "strengthening",
    "bolster": "strengthen",
    "spearheading": "leading",
    "spearhead": "lead",
    "robust": "strong",
    "vibrant": "active",
    "pivotal": "key",
    "crucial": "key",
    "intricate": "detailed",
    "valuable": "useful",
    "profound": "deep",
    "meticulous": "careful",
    "comprehensive": "complete",
    "thorough": "complete",
    "innovative": "new",
    "dedicated": "committed",
    "extensive": "wide",
    "passionate": "keen",
    "seamless": "smooth",
    "tapestry": "mix",
    "landscape": "field",
    "testament": "evidence",
    "delving": "looking",
}

_BUZZWORD_PATTERN = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_BUZZWORD_REPLACEMENTS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_CURLY_QUOTES = str.maketrans({
    "“": '"', "”": '"', "‘": "'", "’": "'",
})


def _match_case(replacement: str, original: str) -> str:
    if original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


_EM_DASH_SEPARATOR = re.compile(r"\s*—\s*")


def _normalize_ai_tells(text: str) -> str:
    """Straight quotes + plain-word substitution for the highest-confidence
    AI-tell vocabulary, plus em-dash-as-separator flattening. Runs on model
    output only, never on evidence/job-post text, and never removes
    content — every substitution preserves the sentence's grammar and
    meaning, just not its exact wording.

    The em-dash rule is prompt-only guidance above ("use commas and full
    stops instead") but live output still used " — " as a phrase separator
    in several places (e.g. "Aston University — MSc Human Resources
    Management, 2022") despite the instruction — this mechanical
    replacement with ", " is safe because the observed pattern is always a
    spaced em-dash standing in for a comma between two phrases, never a
    mid-word dash."""
    text = text.translate(_CURLY_QUOTES)
    text = _EM_DASH_SEPARATOR.sub(", ", text)
    return _BUZZWORD_PATTERN.sub(
        lambda m: _match_case(_BUZZWORD_REPLACEMENTS[m.group(1).lower()], m.group(1)),
        text,
    )


def _filter_rewritten_experience(
    experience: list[dict], cv_text: str, job_post_text: str
) -> tuple[list[dict], list[str], list[str]]:
    """Apply both truthfulness safety nets to every bullet of every role in
    the structured rewrittenExperience array — the same claim the markdown
    safety nets would strip must not survive untouched here just because
    it arrived as a schema field instead of a markdown line. A role that
    loses every bullet this way is dropped entirely: an empty experience
    card is worse than an absent one, mirroring _drop_empty_sections'
    rule for the markdown."""
    kept_roles: list[dict] = []
    all_lifted: list[str] = []
    all_locations: list[str] = []
    for role in experience:
        bullets = list(role.get("bullets") or [])
        bullets, lifted = _filter_lifted_texts(bullets, cv_text, job_post_text)
        bullets, locations = _filter_invented_locations(bullets, cv_text)
        all_lifted += lifted
        all_locations += locations
        if bullets:
            kept_roles.append({**role, "bullets": bullets})
    return kept_roles, all_lifted, all_locations


def _build_information_needed(
    lifted: list[str], removed_locations: list[str]
) -> list[str]:
    """Turns what the safety nets stripped into user-facing questions.
    Shared by _apply_safety_nets (markdown) and _apply_structured_safety_nets
    (rewrittenExperience/suggestedAdditions) so the same underlying claim
    produces the same question regardless of which field it was caught in."""
    information_needed: list[str] = []
    if lifted:
        for claim in lifted:
            information_needed.insert(
                0,
                f'Can you confirm "{claim}"? The job post asks for it and '
                "your CV does not mention it, so it was removed from the "
                "draft rather than claimed on your behalf.",
            )
    if removed_locations:
        shown = ", ".join(sorted(set(removed_locations)))
        information_needed.insert(
            0,
            "Where are you based, and are you able to work in the role's "
            f"location? Your CV does not say, so the draft added {shown}, "
            "which has been removed. Tell us and it can be stated correctly.",
        )
    return information_needed


def _apply_safety_nets(
    markdown: str, cv_text: str, job_post_text: str
) -> tuple[str, list[str]]:
    """Run both code-side truthfulness backstops and build the
    information_needed list purely from what they removed — this list no
    longer comes from the model (that moved to resume_analysis.py's
    matchNotes/informationNeeded), only from what code caught."""
    markdown, lifted = _strip_lifted_requirements(markdown, cv_text, job_post_text)
    markdown, removed_locations = _strip_invented_locations(markdown, cv_text)
    markdown = _normalize_ai_tells(markdown)
    return markdown, _build_information_needed(lifted, removed_locations)


def _apply_structured_safety_nets(
    rewritten_experience: list[dict],
    suggested_additions: list[str],
    cv_text: str,
    job_post_text: str,
) -> tuple[list[dict], list[str], list[str]]:
    """The structured-field counterpart of _apply_safety_nets — sync path
    only (v5 schema fields the streamed path doesn't request)."""
    experience, exp_lifted, exp_locations = _filter_rewritten_experience(
        rewritten_experience, cv_text, job_post_text
    )
    additions, add_lifted = _filter_lifted_texts(
        suggested_additions, cv_text, job_post_text
    )
    additions, add_locations = _filter_invented_locations(additions, cv_text)
    information_needed = _build_information_needed(
        exp_lifted + add_lifted, exp_locations + add_locations
    )
    experience = [
        {**role, "bullets": [_normalize_ai_tells(b) for b in role["bullets"]]}
        for role in experience
    ]
    additions = [_normalize_ai_tells(a) for a in additions]
    return experience, additions, information_needed


def rewrite_resume(
    *,
    cv_text: str,
    job_post_text: str,
    target_title: str | None = None,
    candidate_notes: str | None = None,
    analysis: dict | None = None,
    llm_client_override=None,
) -> ResumeRewriteResult:
    """Run the rewrite synchronously (whole result, no streaming) — kept
    for callers that don't need progressive output (tests, a future
    non-HTTP caller). The live endpoint uses stream_rewrite_resume below.

    No retry-with-correction loop: without an evidence pool there is no
    machine-checkable rejection criterion to feed back, so a retry would
    just be a second roll of the dice. generate_structured already
    retries transient API errors internally.
    """
    if not cv_text or not cv_text.strip():
        raise ResumeRewriteError("No CV text to work from.")
    if not job_post_text or not job_post_text.strip():
        raise ResumeRewriteError("No job post text to work from.")

    payload = prompts.build_user_payload(
        cv_text=cv_text,
        job_post_text=job_post_text,
        target_title=target_title,
        candidate_notes=candidate_notes,
        analysis=analysis,
    )

    try:
        result = generate_structured(
            system_prompt=prompts.RESUME_REWRITE_SYSTEM_PROMPT,
            user_payload=payload,
            json_schema=prompts.RESUME_REWRITE_JSON_SCHEMA,
            schema_name=prompts.RESUME_REWRITE_TASK,
            # v5 asks for rewrittenExperience + suggestedAdditions on top of
            # the same content already in tailoredResumeMarkdown — raised
            # from 3000 so the structured mirror of the experience section
            # doesn't get truncated after the markdown has already used
            # most of the budget.
            max_tokens=4500,
            timeout=settings.openai_timeout_generation_seconds,
            model=settings.openai_model_generation,
            client=llm_client_override,
            prompt_version=prompts.RESUME_REWRITE_PROMPT_VERSION,
        )
    except (LlmCallError, LlmSchemaValidationError) as e:
        LLM_GENERATION_COUNTER.labels(
            generation_task=prompts.RESUME_REWRITE_TASK, outcome="failed",
        ).inc()
        logger.error("resume_rewrite_failed", error=str(e))
        raise ResumeRewriteError(
            "We couldn't finish the rewrite. Please try again in a moment."
        ) from e

    data = result.data
    LLM_GENERATION_COUNTER.labels(
        generation_task=prompts.RESUME_REWRITE_TASK, outcome="succeeded",
    ).inc()
    for token_type, count in (
        ("prompt", result.prompt_tokens),
        ("completion", result.completion_tokens),
    ):
        if count:
            LLM_TOKENS_COUNTER.labels(
                generation_task=prompts.RESUME_REWRITE_TASK, token_type=token_type,
            ).inc(count)

    usage = {
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
    }

    markdown, markdown_information_needed = _apply_safety_nets(
        data.get("tailoredResumeMarkdown") or "", cv_text, job_post_text
    )
    rewritten_experience, suggested_additions, structured_information_needed = (
        _apply_structured_safety_nets(
            data.get("rewrittenExperience") or [],
            data.get("suggestedAdditions") or [],
            cv_text,
            job_post_text,
        )
    )
    information_needed = markdown_information_needed + structured_information_needed

    logger.info(
        "resume_rewrite_complete",
        model=result.model,
        markdown_chars=len(markdown),
        rewritten_experience_roles=len(rewritten_experience),
        suggested_additions=len(suggested_additions),
        information_needed=len(information_needed),
    )

    return ResumeRewriteResult(
        tailored_resume_markdown=markdown,
        information_needed=information_needed,
        usage=usage,
        rewritten_experience=rewritten_experience,
        suggested_additions=suggested_additions,
    )


def stream_rewrite_resume(
    *,
    cv_text: str,
    job_post_text: str,
    target_title: str | None = None,
    candidate_notes: str | None = None,
    analysis: dict | None = None,
    llm_client_override=None,
    usage_sink: dict | None = None,
) -> Iterator[RewriteStreamEvent]:
    """Stream the rewrite as it's generated (jbs-solution-sheet.md S2).

    The code-side safety nets above (_strip_lifted_requirements,
    _strip_invented_locations) need the complete markdown to judge a line
    against — they can't run mid-stream. So this yields raw deltas as they
    arrive for perceived latency, then runs the exact same, unchanged
    safety nets once the model is done. On the rare case they find
    something to remove, it yields one final "corrected" event carrying
    the full cleaned markdown — the caller must replace what it rendered,
    not append. This is strictly rarer and strictly no less safe than the
    non-streaming path: the same functions run against the same complete
    text either way, just after streaming instead of before returning.

    `usage_sink`, if given, is populated in place with
    {"prompt_tokens": int, "completion_tokens": int} once the model's
    final usage-bearing chunk arrives — server-internal accounting (C1's
    spend tracking), not part of the event stream a client sees.
    """
    if not cv_text or not cv_text.strip():
        raise ResumeRewriteError("No CV text to work from.")
    if not job_post_text or not job_post_text.strip():
        raise ResumeRewriteError("No job post text to work from.")

    payload = prompts.build_user_payload(
        cv_text=cv_text,
        job_post_text=job_post_text,
        target_title=target_title,
        candidate_notes=candidate_notes,
        analysis=analysis,
    )

    def _capture_usage(prompt_tokens: int, completion_tokens: int) -> None:
        if usage_sink is not None:
            usage_sink["prompt_tokens"] = prompt_tokens
            usage_sink["completion_tokens"] = completion_tokens

    accumulated: list[str] = []
    try:
        for chunk in stream_text(
            system_prompt=prompts.RESUME_REWRITE_STREAM_SYSTEM_PROMPT,
            user_payload=payload,
            model=settings.openai_model_generation,
            max_tokens=3000,
            timeout=settings.openai_timeout_generation_seconds,
            client=llm_client_override,
            usage_callback=_capture_usage,
            prompt_version=prompts.RESUME_REWRITE_PROMPT_VERSION,
        ):
            accumulated.append(chunk)
            yield RewriteStreamEvent(type="delta", text=chunk)
    except LlmCallError as e:
        LLM_GENERATION_COUNTER.labels(
            generation_task=prompts.RESUME_REWRITE_TASK, outcome="failed",
        ).inc()
        logger.error("resume_rewrite_stream_failed", error=str(e))
        raise ResumeRewriteError(
            "The rewrite was interrupted. Please try again."
        ) from e

    LLM_GENERATION_COUNTER.labels(
        generation_task=prompts.RESUME_REWRITE_TASK, outcome="succeeded",
    ).inc()

    raw_markdown = "".join(accumulated)
    markdown, information_needed = _apply_safety_nets(
        raw_markdown, cv_text, job_post_text
    )

    logger.info(
        "resume_rewrite_stream_complete",
        markdown_chars=len(markdown),
        information_needed=len(information_needed),
        corrected=markdown != raw_markdown,
    )

    if markdown != raw_markdown:
        yield RewriteStreamEvent(
            type="corrected", text=markdown, information_needed=information_needed,
        )
    else:
        yield RewriteStreamEvent(type="done", information_needed=information_needed)
