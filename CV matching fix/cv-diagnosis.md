# Diagnosis: Why the Tailored CV Is Worse Than the Original

**Method:** every claim below was reproduced against the actual code and your actual CV, not inferred. Where I ran a test, the result is shown.

**Verdict up front:** this is not a tuning problem. Four independent architectural decisions each destroy CV content, and they compound. The output isn't a weaker version of a good system — it's a system whose filtering logic is inverted relative to its goal. The good news: three of the four are contained fixes, and the code quality around them is genuinely high (the non-fabrication discipline is real and worth preserving).

---

## Failure 1 — Skills: 27 → 2, and it kept the wrong two

**What you got:** `CSS, HTML`
**What your CV has:** Figma, UX research, usability testing, micro-interaction prototyping, survey design, behavioural analysis, prototype testing, wireframing, interaction design, high-fidelity UI, design systems, component libraries, accessibility, WCAG 2.1, user journeys, information architecture, CRO, Hotjar, UserTesting, analytics-informed design, workshop facilitation, stakeholder management, HTML, CSS, JavaScript, Jira, Confluence.

**Root cause — `tailored_cv_generation.py:67-68`:**
```python
bound = evidence_binder.bind_evidence_pool(match_evidence_items, all_candidates)
skill_candidates = [c for c in bound if c.row_type == evidence_binder.SKILL]
```
Skills only survive if the *matching engine* cited them. The match rule (`evidence_binder.py:139-143`) is:
```python
req_text == cand_text or req_text in cand_text or cand_text in req_text
```

**Why this specifically keeps CSS and drops Figma.** The `cand_text in req_text` branch means a skill survives if its name appears *inside a requirement sentence*. Short generic tokens win; specific valuable ones lose. "HTML" appears inside "Ability to work with HTML and CSS." "Figma" appears inside nothing, because job ads say "prototyping tools," not "Figma."

**Reproduced** against six typical senior-product-designer requirement phrasings: 6 skills matched, **21 dropped**. The survivors were exactly the generic ones.

The system is filtering *out* the skills that qualify you and keeping the two that don't distinguish you at all. For a design role, shipping a CV whose skills section reads "CSS, HTML" is worse than shipping no skills section.

### Fix

**Invert the default: include all skills, use matching to *order* them, not to *exclude*.**

```python
def _generate_skills_section(*, match_evidence_items, all_candidates, order_index):
    all_skills = [c for c in all_candidates if c.row_type == evidence_binder.SKILL]
    if not all_skills:
        return None
    matched_ids = {c.row_id for c in evidence_binder.bind_evidence_pool(
        match_evidence_items, all_candidates) if c.row_type == evidence_binder.SKILL}
    # matched first (job-relevant surfaced), then the rest, original order preserved
    ordered = ([c for c in all_skills if c.row_id in matched_ids]
               + [c for c in all_skills if c.row_id not in matched_ids])
    ordered = ordered[: settings.tailored_cv_max_skill_items]  # suggest 24
    ...
```

This is defensible against the non-fabrication rule: every skill is on the candidate's own CV. Omitting a real skill isn't "safe" — it's lying by omission in the other direction, and it costs you the role.

**Also worth doing:** the matching itself needs semantic help. `skills_index.py` and the ESCO/O*NET ingest scripts already exist in the repo — if "Figma" is a known synonym/child of "prototyping tools" in that index, use it here. That improves *ordering* quality even after the include-by-default fix.

---

## Failure 2 — Education vanished entirely

**Root cause — `worker_jobs.py`, `_parse_education_line`.** The parser recognises education only via two keyword lists:

- Degrees: `Bachelor|Master|PhD|BSc|BA|MSc|MBA|Diploma|Associate|HND|BTech|MTech`
- Institutions: `University|College|Institute|Polytechnic|School of|Academy`

**Tested against your four actual lines:**

| Line | degree kw | institution kw | Result |
|---|---|---|---|
| Google's UX Design Certificate — Coursera | ✗ | ✗ | **discarded** |
| Diploma in Application Development — Karrox Computer Education Centre | ✓ | ✗ | parsed |
| How to Build Digital Products — Product School | ✗ | ✗ | **discarded** |
| User Experience — FutureLearn | ✗ | ✗ | **discarded** |

Three of four hit `return None  # ambiguous — never guess which side is which`.

The education *generator* is correct — I checked it (`_generate_education_section` explicitly uses unfiltered rows and returns a section if either education or certifications is non-empty). The loss is upstream, at parse time. The parser was built for traditional academic CVs and is blind to the entire modern online-credential ecosystem: Coursera, Product School, FutureLearn, Google Certificates, General Assembly, Udacity, edX.

### Fix

1. **Add a certificate/provider keyword list**, not just degrees and universities: `Certificate|Certification|Nanodegree|Bootcamp|Course|Specialization|Coursera|Udacity|edX|FutureLearn|LinkedIn Learning|Product School|General Assembly|Google|Meta|AWS|Microsoft`.
2. **Change the fallback from discard to preserve.** When neither side can be classified, don't return `None` — return the line with `degree=<full line>, institution=None, confidence=0.3`. A low-confidence education entry that renders correctly beats a silently-dropped real qualification. The current behaviour prioritises structural purity over not losing the user's actual credentials; that trade is backwards for a CV tool.
3. Route unclassifiable-but-present lines into the certifications list, which already merges into the education section.

---

## Failure 3 — Experience flattened from 15 bullets to 1 paragraph

**Root cause — `generation_core.py:34-39`:**
```python
"properties": {
    "contentText": {"type": "string"},        # ← single string
    "evidenceIndexes": {"type": "array", ...},
},
```

The LLM is *structurally incapable* of returning multiple bullets. One string per experience item means your OSB role — 15 bullets across three distinct workstreams (Charter Savings Bank redesign, InterBay design system, mentorship) — must be compressed into one paragraph. It kept the first two sentences of CSB and discarded the rest, including the entire InterBay design-system work (tokens, component libraries, WCAG reasoning) which is arguably your strongest evidence for a senior design role.

**Also lost entirely:** the ADPList mentoring role and the whole "Earlier Career" block. The `tailored_cv_max_experience_items` cap of 6 wasn't the binding constraint (only 4 roles rendered) — those roles were dropped by relevance ranking, not the cap.

### Fix

**Change the schema to return structured bullets:**
```python
_SECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "bullets": {
            "type": "array",
            "minItems": 2,
            "maxItems": 6,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "evidenceIndexes": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "evidenceIndexes"],
            },
        },
    },
    "required": ["bullets"],
}
```

Evidence verification then runs **per bullet** rather than per paragraph — which is also *stricter*, since each claim must independently cite and pass. You get more detail and tighter grounding simultaneously.

**Also:** don't silently drop whole roles. A senior CV needs continuity. Add a condensed "Earlier Career" line for sub-threshold roles rather than omitting them (your original CV already does exactly this — the system threw it away).

---

## Failure 4 — No name, no phone, no portfolio (blocking)

`export_rendering.py:11-16` states it plainly: *"the parser has never extracted a candidate's name or contact details from the document text, only the summary."*

The renderer degrades gracefully and falls back to the account login email rather than fabricating — that's the right instinct. But the outcome is a document with **no candidate name on it**. That isn't a CV. It cannot be sent to a recruiter under any circumstances.

### Fix

Add a header-block parser: the first 3-5 lines above the first canonical section heading are, on essentially every CV, the name and contact block. Extract:
- **Name** — first non-empty line, typically title-case or all-caps, no digits, 2-4 words
- **Email** — regex
- **Phone** — regex, international formats
- **URLs** — portfolio/LinkedIn
- **Location** — remaining short segment

Your CV's first three lines are a textbook case: `TOBILOBA ODU` / `PRODUCT DESIGNER, UI/UX and RESEARCH` / `tobilobaodu.com | oduoluwatobi@gmail.com | +447562695548`. All of it is trivially recoverable and currently thrown away.

---

## The deeper issue: the evidence-overlap gate fights quality

`tailored_cv_evidence_overlap_threshold = 0.35` requires generated text to share ≥35% token overlap with its source rows.

This is a well-intentioned non-fabrication guard, and I want to be clear that the underlying discipline is right and worth keeping. But as implemented it **penalises good rewriting**. Compare:

- Original: "Defined accessibility rules for typography and colour usage"
- Genuinely better: "Established WCAG 2.1-compliant typographic and colour standards adopted across five lending products"

The second is stronger and entirely truthful — and has *lower* token overlap. The model's safest strategy under this gate is near-copying, which is exactly what your output shows: lightly reworded original sentences.

**You cannot get Harvard-standard writing while the primary quality gate rewards similarity to the input.**

### Fix: verify claims, not tokens

Replace token-overlap with **entity/claim verification** — extract from the generated text and confirm each appears in the cited evidence:
- Numbers and percentages (`25%`, `43 issues`, `20 participants`, `seven brands`)
- Employers, products, tools, technologies
- Dates and durations
- Job titles

Fail if the generated text contains a number, employer, or technology **not present in the evidence**. Pass otherwise, regardless of phrasing overlap. This blocks fabrication (the actual risk) while permitting genuine rewriting (the actual goal). It's stricter where it matters and looser where it doesn't.

---

## What "Harvard-style" actually requires, and what's missing

The convention recruiters respond to is: **strong action verb + specific action + quantified outcome**, one line each, most impressive first.

> "Led end-to-end UX redesign of Charter Savings Bank's product shopping experience, identifying 43 navigation and conversion issues through heuristic audit; validated the simplified journey with 20 UK participants, removing a major conversion barrier."

Your CV already contains this material. Four things currently prevent it reaching the output:

1. **Bullets, not paragraphs** — Failure 3's schema fix. Prerequisite for everything else.
2. **Quantification must be preserved and prioritised.** Your CV is full of numbers (43 issues, 20 participants, 25%, 50%, seven brands, five products). The current prompt doesn't explicitly instruct the model to preserve and lead with them. Add that instruction, and add a claim-verification rule that numbers must trace to evidence (which the Failure-4 fix gives you anyway).
3. **Verb-first construction.** Add to the prompt: every bullet opens with a strong past-tense action verb; never "Responsible for," "Worked on," "Helped with."
4. **Ordering within a role.** Highest-impact bullet first, not source order. Currently ordering is inherited from the CV.

Worth adding to `tailored_cv_prompts.py` alongside the schema change — they're complementary, and the schema change is what makes the prompt guidance actionable.

---

## Recommended order

| # | Fix | Effort | Impact |
|---|---|---|---|
| 1 | Header/contact parser | Small | **Blocking** — document is unsendable without it |
| 2 | Skills: include-all, order-by-match | Small | Very high — fixes the most visible failure |
| 3 | Education: certificate keywords + preserve-on-ambiguity | Small | High |
| 4 | Bullets schema (`contentText` → `bullets[]`) | Medium | Very high — unlocks detail *and* per-bullet verification |
| 5 | Prompt: verb-first, quantify, impact-ordered | Small | High — depends on #4 |
| 6 | Claim verification replacing token overlap | Medium-large | High — unlocks genuine rewriting |
| 7 | Preserve sub-project grouping; condensed earlier-career line | Medium | Medium |

Items 1-3 are each an afternoon and would transform the output on their own. Item 4 is the structural unlock. Item 6 is the one that gets you from "accurate" to "impressive," and is worth doing carefully with its own test coverage — `test_evidence_binder.py` already exists as a foundation.

---

## One thing worth preserving

The non-fabrication architecture is genuinely good work — schema-constrained output, mandatory evidence citation, omit-rather-than-substitute on verification failure, a CHECK constraint enforcing non-empty evidence references at the database level. That discipline is rare and it's the right foundation.

The problem isn't that the system is too careful. It's that **caution was implemented as deletion** — dropping skills, dropping education, compressing bullets, dropping roles. Every one of the fixes above keeps the non-fabrication guarantee fully intact while changing the failure mode from "silently remove the candidate's real qualifications" to "include everything real, verified, and well-written."
