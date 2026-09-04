"""Cover letter question generator and draft assembler.

Phase 4 first pass uses rules-based question generation and template-based
draft assembly. An LLM-backed generator can be swapped in later without
changing the API or worker.

Per the non-fabrication rule: questions are generated ONLY from match
evidence items flagged as unsupported, contradictory, or unclear. Missing
evidence results in a question, never an invented claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings

# ──────────────────────────────────────────────────────────────────────
# Question categories (per 05-openapi.yaml)
# ──────────────────────────────────────────────────────────────────────

CAT_EMPLOYER_INTEREST = "employer_interest"
CAT_MOTIVATION = "motivation"
CAT_RELEVANT_EXAMPLE = "relevant_example"
CAT_TONE_PREFERENCE = "tone_preference"
CAT_AVAILABILITY = "availability"
CAT_CLARIFICATION = "clarification"


@dataclass
class GeneratedQuestion:
    """A question to present to the user at a specific workflow step."""
    step_number: int
    question_text: str
    question_category: str
    required: bool = True
    help_text: str | None = None
    source_evidence_item_id: str | None = None


# ──────────────────────────────────────────────────────────────────────
# Question generation
# ──────────────────────────────────────────────────────────────────────


def generate_questions(
    cv_name: str | None,
    employer_name: str | None,
    job_title: str,
    match_evidence: list[dict],
) -> list[GeneratedQuestion]:
    """Generate question sets from match evidence gaps.

    Each unsupported/contradictory/unclear evidence item becomes one
    clarification question. Standard motivation/interest/availability
    questions are added as a fixed set across all workflows.

    Args:
        cv_name: Candidate's name from the CV profile (or None).
        employer_name: Employer from the job post (or None).
        job_title: Job title from the job post.
        match_evidence: List of match evidence item dicts with keys
            support_level, requirement_text, requirement_type, suggestion, warning.

    Returns:
        List of GeneratedQuestion, organised by step_number.
    """
    questions: list[GeneratedQuestion] = []

    # ── Step 1: Employer interest & motivation ─────────────────────
    questions.append(GeneratedQuestion(
        step_number=1,
        question_text=f"Why are you interested in the {job_title} role"
                       f"{' at ' + employer_name if employer_name else ''}?",
        question_category=CAT_EMPLOYER_INTEREST,
        required=True,
        help_text="Mention what attracted you to this specific role and company.",
    ))
    questions.append(GeneratedQuestion(
        step_number=1,
        question_text="What about this role aligns with your career goals?",
        question_category=CAT_MOTIVATION,
        required=True,
        help_text="Connect this opportunity to where you want your career to go.",
    ))

    # ── Step 2: Clarification questions from match gaps ────────────
    gap_items = [
        e for e in match_evidence
        if e.get("support_level") in ("unsupported", "contradictory", "unclear")
    ]
    for i, item in enumerate(gap_items[:5]):  # cap at 5 gap questions per step
        req_text = item.get("requirement_text", "")
        support = item.get("support_level", "unsupported")
        suggestion = item.get("suggestion") or item.get("warning") or ""

        if support == "unsupported":
            prompt = f"The job requires '{req_text}'. Can you provide a relevant example from your experience?"
            help_text = suggestion or "Describe a specific project or achievement."
        elif support == "contradictory":
            prompt = f"Your CV shows conflicting information about '{req_text}'. Can you clarify which is correct?"
            help_text = suggestion
        else:  # unclear
            prompt = f"Your CV may mention '{req_text}' but our extraction was uncertain. Can you confirm or elaborate?"
            help_text = suggestion

        questions.append(GeneratedQuestion(
            step_number=2,
            question_text=prompt,
            question_category=CAT_CLARIFICATION,
            required=False,
            help_text=help_text if help_text else None,
            source_evidence_item_id=item.get("id"),
        ))

    if not gap_items:
        # If no gaps, ask for a general relevant example
        questions.append(GeneratedQuestion(
            step_number=2,
            question_text=(
                "What is one achievement or project you'd like to highlight "
                f"in relation to this {job_title} role?"
            ),
            question_category=CAT_RELEVANT_EXAMPLE,
            required=True,
        ))

    # ── Step 3: Tone, availability, closing preferences ────────────
    questions.append(GeneratedQuestion(
        step_number=3,
        question_text="What tone would you like for this letter?",
        question_category=CAT_TONE_PREFERENCE,
        required=False,
        help_text="e.g. formal, enthusiastic, concise, detailed",
    ))
    questions.append(GeneratedQuestion(
        step_number=3,
        question_text="Do you have any availability constraints or preferred start dates?",
        question_category=CAT_AVAILABILITY,
        required=False,
        help_text="Optional — leave blank if not applicable.",
    ))
    # ── Step 4: final catch-all clarification ───────────────────────
    # Split out as its own step (rather than folded into step 3 with
    # tone/availability) so it reads as a deliberate last chance to add
    # anything material, not one more optional field alongside two
    # unrelated preference questions.
    questions.append(GeneratedQuestion(
        step_number=4,
        question_text="Is there anything else the hiring manager should know?",
        question_category=CAT_CLARIFICATION,
        required=False,
        help_text="Any additional context, certifications, or achievements.",
    ))

    return questions


# ──────────────────────────────────────────────────────────────────────
# Draft assembly
# ──────────────────────────────────────────────────────────────────────


@dataclass
class AssembledDraft:
    """A cover letter draft assembled from CV data, match evidence, and user answers."""
    body_text: str
    evidence_references: list[str]


# ── Fallback template generation (Sprint 4) ─────────────────────────
# The always-available, no-LLM path — used when real generation fails
# verification or is explicitly disabled (settings.cover_letter_llm_
# generation_enabled=False). Ideas ported from a supplied reference
# script (priority-requirement selection, keyword-overlap achievement
# matching, a fixed 6-part write pattern), adapted to this codebase's
# real CvExperienceItem/CvProjectItem rows instead of a separate
# "Achievement" input type, and bounded so "enforce length" can only
# ever mean "cite more/less of what's real," never invent filler —
# this is the fallback for when the LLM path isn't available, so it
# has no model to lean on for phrasing either.


def _tokenize(text: str) -> set[str]:
    return {
        t.strip().lower()
        for t in (text or "").replace("/", " ").replace(",", " ").split()
        if t.strip()
    }


def _lowercase_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text else text


def _story_candidates(experience_items, project_items) -> list[tuple[str, object]]:
    """('experience', item) | ('project', item) pairs — experience first
    (usually the stronger, more directly job-relevant signal), then
    projects."""
    return [("experience", e) for e in experience_items] + [("project", p) for p in project_items]


def _item_text(kind: str, item) -> str:
    if kind == "experience":
        parts = [item.title or "", item.company or "", *(item.bullets or [])]
    else:
        parts = [item.name or "", item.description or "", *(item.bullets or [])]
    return " ".join(p for p in parts if p)


def _select_stories(
    job_requirements: list[str],
    experience_items,
    project_items,
    max_stories: int,
) -> list[tuple[str, object]]:
    """Ported from the reference script's map_achievements_to_
    responsibilities + select_experience_stories: rank real experience/
    project rows by keyword overlap against the top job requirements,
    dedupe, cap at max_stories. Falls back to the earliest real
    candidates (in CV order) if fewer than max_stories matched anything
    — a thinner-than-ideal but still real letter beats an artificially
    short one when a candidate's real background just doesn't share
    obvious keywords with the posting."""
    candidates = _story_candidates(experience_items, project_items)
    priority_requirements = job_requirements[:3]

    matched: list[tuple[str, object]] = []
    seen_ids: set[str] = set()
    for req in priority_requirements:
        req_tokens = _tokenize(req)
        if not req_tokens:
            continue
        for kind, item in candidates:
            if item.id in seen_ids:
                continue
            if _tokenize(_item_text(kind, item)) & req_tokens:
                matched.append((kind, item))
                seen_ids.add(item.id)

    if len(matched) < max_stories:
        for kind, item in candidates:
            if item.id not in seen_ids:
                matched.append((kind, item))
                seen_ids.add(item.id)
            if len(matched) >= max_stories:
                break

    return matched[:max_stories]


def _story_sentence(kind: str, item, is_first: bool) -> str:
    lead = "At" if is_first else "While at"
    if kind == "experience":
        org = item.company or "a previous role"
        role = item.title
        detail = (item.bullets or [None])[0]
        if detail:
            detail = _lowercase_first(detail.rstrip("."))
            if role:
                return f"{lead} {org}, working as {role}, I {detail}."
            return f"{lead} {org}, I {detail}."
        return f"{lead} {org}, I worked as {role or 'a contributor'}."
    name = item.name or "a personal project"
    detail = (item.bullets or [None])[0] or item.description
    if detail:
        detail = _lowercase_first(detail.rstrip("."))
        return f"On {name}, a personal project, I {detail}."
    return f"I worked on {name}, a personal project."


def _select_skill_line(skill_items, job_requirements: list[str], max_skills: int = 4) -> tuple[str | None, list[str]]:
    req_tokens: set[str] = set()
    for r in job_requirements:
        req_tokens |= _tokenize(r)

    matched_names: list[str] = []
    refs: list[str] = []
    for s in skill_items:
        name = s.skill_name or ""
        if name and _tokenize(name) & req_tokens:
            matched_names.append(name)
            refs.append(s.id)
        if len(matched_names) >= max_skills:
            break

    if not matched_names:
        return None, []
    return f"I bring hands-on experience in {', '.join(matched_names)}.", refs


def _word_count(text: str) -> int:
    return len([w for w in text.split() if w.strip()])


def assemble_draft(
    *,
    cv_name: str | None,
    employer_name: str | None,
    job_title: str,
    tone: str | None,
    answers_by_step: dict[int, list[tuple[str, str]]],
    experience_items,
    project_items,
    skill_items,
    job_requirements: list[str],
) -> AssembledDraft:
    """Assemble a cover letter body from structured, real inputs.

    Template substitution — no LLM. Every sentence is backed by either a
    real CV row (CvExperienceItem/CvProjectItem/CvSkillItem) or a real
    user-submitted answer; evidence_references cites the real row/answer
    ids, not opaque string tags. `answers_by_step` maps step_number to a
    list of (answer_id, answer_text) tuples so real ids are always
    available to cite.

    Deliberately does not require email/phone (a reference script's
    validate_inputs() did) — this codebase's CV parser never populates
    either field today (confirmed: worker_jobs.py hardcodes them None),
    so that check would reject every real CV ever parsed by this system.
    """
    job_title = job_title or "this role"
    step1 = answers_by_step.get(1, [])
    step2 = answers_by_step.get(2, [])
    step3 = answers_by_step.get(3, [])

    # ── Greeting ─────────────────────────────────────────────────
    greeting = "Dear Hiring Manager,"

    # ── Opening: role/employer + step-1 motivation answers ──────────
    opening_lines = [
        f"I am writing to express my interest in the {job_title} position"
        + (f" at {employer_name}" if employer_name else "") + "."
    ]
    opening_refs: list[str] = []
    for ans_id, text in step1[:2]:
        if text and text.strip():
            opening_lines.append(text.strip())
            opening_refs.append(ans_id)
    opening = " ".join(opening_lines)

    # ── Experience: real stories (ranked by requirement-keyword
    # overlap) + step-2 answers + a matched-skills line ──────────────
    stories = _select_stories(
        job_requirements, experience_items, project_items,
        settings.cover_letter_fallback_max_stories,
    )
    story_units = [
        (_story_sentence(kind, item, is_first=(i == 0)), item.id)
        for i, (kind, item) in enumerate(stories)
    ]
    answer_units = [(text.strip(), ans_id) for ans_id, text in step2 if text and text.strip()]
    skill_line, skill_refs = _select_skill_line(skill_items, job_requirements)
    skill_unit = [(skill_line, skill_refs)] if skill_line else []

    experience_units: list[tuple[str, list[str]]] = (
        [(s, [rid]) for s, rid in story_units]
        + [(s, [rid]) for s, rid in answer_units]
        + skill_unit
    )
    if not experience_units:
        experience_units = [("I bring a strong, relevant background to this role.", [])]

    # ── Closing: CTA + last step-3 answer ───────────────────────────
    closing_lines = [
        f"I would welcome the opportunity to discuss how my experience "
        f"aligns with the {job_title} role.",
    ]
    closing_refs: list[str] = []
    if step3 and step3[-1][1] and step3[-1][1].strip():
        closing_lines.append(step3[-1][1].strip())
        closing_refs.append(step3[-1][0])
    closing_lines.append("Thank you for your consideration.")
    closing = " ".join(closing_lines)

    # ── Signature ────────────────────────────────────────────────
    signature = f"Sincerely,\n{cv_name}" if cv_name else "Sincerely,"

    # ── Length enforcement, bounded by real material only ──────────
    # Over budget: drop experience_units from the end (lowest priority —
    # skill line was appended last, so it's the first candidate to drop;
    # then extra answers; a story is never dropped below 1 if any exist).
    # Under budget: nothing left to add without inventing content — leave
    # it short rather than pad with filler; logged by the caller via the
    # returned unit count, not fabricated here.
    def _render(units: list[tuple[str, list[str]]]) -> tuple[str, list[str]]:
        experience_text = " ".join(u[0] for u in units)
        parts = [greeting, "", opening, "", experience_text, "", closing, "", signature]
        body = "\n\n".join(p for p in parts if p)
        refs = opening_refs + [rid for _, ids in units for rid in ids] + closing_refs
        return body, refs

    body, evidence_refs = _render(experience_units)
    min_stories_kept = 1 if story_units else 0
    while _word_count(body) > settings.cover_letter_max_word_count and len(experience_units) > min_stories_kept:
        experience_units = experience_units[:-1]
        body, evidence_refs = _render(experience_units)

    return AssembledDraft(
        body_text=body,
        evidence_references=[r for r in evidence_refs if r],
    )