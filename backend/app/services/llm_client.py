"""Thin, injectable wrapper around the OpenAI SDK for structured generation.

First LLM integration in this codebase — no existing call/retry/schema-
validation precedent to copy (cover_letter.py's assemble_draft() is pure
string templating, zero model calls). This module is the seam every
generation call goes through, so it's built to be easily mockable in
tests (no real API calls anywhere in the test suite) and to enforce
02-architecture-overview.md §6's requirements directly: schema-constrained
output (JSON Schema strict mode, not free text), and "validation and
retry, not silent correction" — this module retries transient API errors
(network/timeout/rate-limit) itself, but a schema-valid-yet-unverified
response is the CALLER's retry to make (a corrective re-prompt with the
specific failure), not this module's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable, Iterator

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from app.core.config import settings
from app.core.logging import get_logger
from app.core.circuit_breaker import OPENAI_CIRCUIT
from app.core.metrics import LLM_TOKENS_COUNTER
from app.core.tracing import tracer

logger = get_logger(__name__)

# O3: per-token USD rate for the model actually used in this codebase
# (settings.openai_model/openai_model_generation both point at gpt-5-mini
# today). Mirrors the rate app/api/v1/resume_rewrites.py's
# _GPT_5_MINI_RATE already uses for real budget accounting — duplicated
# here rather than imported, since this module is a lower layer neither
# of those API-layer modules should import from in the other direction.
# Unifying into one shared pricing source is reasonable future cleanup,
# not done here — this table exists only to label the tracing span's
# cost_usd attribute, it is not itself a billing/budget source of truth.
# Rate confirmed against OpenAI's pricing page: $0.25/M input,
# $2.00/M output.
_MODEL_PRICING_PER_TOKEN = {
    "gpt-5-mini": (0.25 / 1_000_000, 2.00 / 1_000_000),  # (prompt, completion)
}


def _estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    rates = _MODEL_PRICING_PER_TOKEN.get(model)
    if rates is None:
        return None
    prompt_rate, completion_rate = rates
    return prompt_tokens * prompt_rate + completion_tokens * completion_rate


class LlmCallError(Exception):
    """Transient/infra-level failure (network, timeout, rate limit, or a
    non-retryable API error) after exhausting retries."""


class LlmSchemaValidationError(Exception):
    """The response was not valid JSON, or the model refused to answer.
    Distinct from LlmCallError — this is a content problem, not a
    connectivity problem, and callers should treat it as a signal to
    retry with a corrective prompt, not to blindly resend the same call.
    """


@dataclass
class StructuredGenerationResult:
    data: dict
    prompt_tokens: int
    completion_tokens: int
    model: str


_TRANSIENT_EXCEPTIONS = (APIConnectionError, APITimeoutError, RateLimitError)


def _get_client(timeout: float | None = None) -> OpenAI:
    return OpenAI(
        api_key=settings.openai_api_key,
        timeout=timeout if timeout is not None else settings.openai_request_timeout_seconds,
    )


def generate_structured(
    *,
    system_prompt: str,
    user_payload: str,
    json_schema: dict,
    schema_name: str,
    model: str | None = None,
    max_tokens: int = 1500,
    timeout: float | None = None,
    max_api_retries: int = 2,
    client: OpenAI | None = None,
    prompt_version: str | None = None,
) -> StructuredGenerationResult:
    """Call the chat completions API in JSON-schema strict mode.

    Message structure is a deliberate instruction/data split per
    10-security-plan.md §5: system_prompt is fixed template text only
    (never CV/job-post content); user_payload carries the untrusted,
    explicitly-framed data block. Callers (tailored_cv_generation.py) are
    responsible for building user_payload with that framing — this
    function doesn't inspect or modify either string.

    `client` is injectable so tests never construct a real OpenAI client
    (which would fail immediately without an API key) — pass a fake with
    a matching `.chat.completions.create` surface instead.

    `prompt_version` (O3): optional, purely a tracing-span attribute — the
    caller's own PROMPT_VERSION constant, when it has one. Optional
    rather than required so adopting tracing doesn't force every call
    site to change atomically; omit it and the span just won't carry
    that attribute.
    """
    with tracer.start_as_current_span("llm.generate_structured") as span:
        span.set_attribute("llm.schema_name", schema_name)
        if prompt_version:
            span.set_attribute("llm.prompt_version", prompt_version)

        client = client or _get_client(timeout)
        model = model or settings.openai_model
        span.set_attribute("llm.model", model)

        # Circuit breaker (§6): fail fast rather than queuing a call that will
        # only time out and hold worker capacity while the provider is degraded.
        if not OPENAI_CIRCUIT.allow():
            raise LlmCallError(
                "LLM circuit open — failing fast rather than attempting a call "
                "that will only time out."
            )

        last_error: Exception | None = None
        response = None
        for attempt in range(max_api_retries + 1):
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_payload},
                    ],
                    # gpt-5-mini-class models reject the older max_tokens
                    # param name and require max_completion_tokens — this
                    # function's own max_tokens= parameter is unchanged,
                    # only the outgoing API request key is renamed.
                    max_completion_tokens=max_tokens,
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema_name,
                            "schema": json_schema,
                            "strict": True,
                        },
                    },
                )
                break
            except _TRANSIENT_EXCEPTIONS as e:
                last_error = e
                logger.warning(
                    "llm_transient_error", attempt=attempt, error=str(e), schema_name=schema_name,
                )
                continue
            except APIError as e:
                # Non-transient (bad request, auth, content policy, etc.) — no retry.
                raise LlmCallError(f"OpenAI API error: {e}") from e

        if response is None:
            OPENAI_CIRCUIT.record_failure()
            raise LlmCallError(
                f"OpenAI API call failed after {max_api_retries + 1} attempts: {last_error}"
            )

        # The dependency responded — that's a circuit success, regardless of what
        # content-level validation below decides about the payload.
        OPENAI_CIRCUIT.record_success()

        choice = response.choices[0]
        message = choice.message

        if message.refusal:
            raise LlmSchemaValidationError(f"Model refused to generate: {message.refusal}")

        if not message.content:
            raise LlmSchemaValidationError("Model returned empty content")

        # With strict:true, hitting max_tokens truncates mid-JSON and json.loads
        # below raises a generic "not valid JSON" — which sends you looking for
        # a model problem that is really a config problem (the cap set too low
        # for this schema/input). Name it explicitly instead.
        if getattr(choice, "finish_reason", None) == "length":
            logger.warning(
                "llm_output_truncated", schema_name=schema_name, max_tokens=max_tokens,
            )
            raise LlmSchemaValidationError(
                "Response hit the token cap before completing."
            )

        try:
            data = json.loads(message.content)
        except json.JSONDecodeError as e:
            raise LlmSchemaValidationError(f"Response was not valid JSON: {e}") from e

        usage = response.usage
        prompt_tokens = usage.prompt_tokens if usage else 0
        completion_tokens = usage.completion_tokens if usage else 0
        span.set_attribute("llm.input_tokens", prompt_tokens)
        span.set_attribute("llm.output_tokens", completion_tokens)
        cost_usd = _estimate_cost_usd(model, prompt_tokens, completion_tokens)
        if cost_usd is not None:
            span.set_attribute("llm.cost_usd", cost_usd)

        return StructuredGenerationResult(
            data=data,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=response.model,
        )


def stream_text(
    *,
    system_prompt: str,
    user_payload: str,
    model: str | None = None,
    max_tokens: int = 3000,
    timeout: float | None = None,
    generation_task: str = "resume_rewrite",
    client: OpenAI | None = None,
    usage_callback: "Callable[[int, int], None] | None" = None,
    prompt_version: str | None = None,
) -> Iterator[str]:
    """Stream a plain-text/markdown completion. No JSON schema — the
    caller wants readable output as it arrives, which a strict-schema
    response cannot give (it is unparseable until the closing brace). Do
    not use this for anything the caller needs to parse as structured
    data; use generate_structured for that.

    A sibling to generate_structured, not a modification of it: six
    callers depend on that function's retry/circuit-breaker contract, and
    a stream can't retry transparently anyway (see below).

    Deliberately no retry: a stream that fails mid-flight has already
    sent bytes to the client, so a silent retry would duplicate content.
    The caller surfaces the break (LlmCallError) and offers a re-run.

    `generation_task` labels the token-usage metric — passed explicitly
    rather than inferred, since (unlike generate_structured, which infers
    it from schema_name) there's no schema here to name the task.

    `usage_callback(prompt_tokens, completion_tokens)`, if given, is
    called once when the final usage-bearing chunk arrives — for a caller
    that needs real token counts for something beyond the metric here
    (e.g. per-identity spend tracking, jbs-solution-sheet.md C1), since a
    generator's yielded values are text chunks, not a place to also
    return usage.
    """
    with tracer.start_as_current_span("llm.stream_text") as span:
        span.set_attribute("llm.generation_task", generation_task)
        if prompt_version:
            span.set_attribute("llm.prompt_version", prompt_version)

        client = client or _get_client(timeout)
        model = model or settings.openai_model
        span.set_attribute("llm.model", model)

        if not OPENAI_CIRCUIT.allow():
            raise LlmCallError(
                "LLM circuit open — failing fast rather than attempting a call "
                "that will only time out."
            )

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_payload},
                ],
                # See the matching comment in generate_structured above.
                max_completion_tokens=max_tokens,
                stream=True,
                # Without this the final chunk carrying usage never arrives,
                # and LLM_TOKENS_COUNTER silently stops counting generation
                # tokens — easy to miss, and it breaks cost tracking without
                # breaking the feature itself.
                stream_options={"include_usage": True},
            )
            for chunk in stream:
                if chunk.usage:
                    LLM_TOKENS_COUNTER.labels(
                        generation_task=generation_task, token_type="completion",
                    ).inc(chunk.usage.completion_tokens)
                    LLM_TOKENS_COUNTER.labels(
                        generation_task=generation_task, token_type="prompt",
                    ).inc(chunk.usage.prompt_tokens)
                    span.set_attribute("llm.input_tokens", chunk.usage.prompt_tokens)
                    span.set_attribute("llm.output_tokens", chunk.usage.completion_tokens)
                    cost_usd = _estimate_cost_usd(
                        model, chunk.usage.prompt_tokens, chunk.usage.completion_tokens,
                    )
                    if cost_usd is not None:
                        span.set_attribute("llm.cost_usd", cost_usd)
                    if usage_callback:
                        usage_callback(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                if chunk.choices and chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
            OPENAI_CIRCUIT.record_success()
        except _TRANSIENT_EXCEPTIONS as e:
            OPENAI_CIRCUIT.record_failure()
            raise LlmCallError(f"Stream failed: {e}") from e
        except APIError as e:
            OPENAI_CIRCUIT.record_failure()
            raise LlmCallError(f"OpenAI API error: {e}") from e
