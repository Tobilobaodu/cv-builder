"""Circuit breaker tests — pure state-machine units plus a wiring test that
proves the breaker is actually connected in the real LLM call path (not just
an unused module — this project has already shipped the "a Celery task exists
but isn't wired to a queue" class of bug, and this guards against repeating it
for the circuit breaker).

Uses a fake clock so no test sleeps through a real cooldown window.
"""

from unittest.mock import MagicMock

import httpx
import pytest
from openai import APIConnectionError

from app.core.circuit_breaker import CircuitBreaker, CircuitOpenError, OPENAI_CIRCUIT
from app.services.llm_client import LlmCallError, generate_structured


def _connection_error():
    """openai 2.x's APIConnectionError requires a request kwarg."""
    return APIConnectionError(request=httpx.Request("GET", "https://api.openai.com/v1/chat/completions"))


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def _breaker(threshold=5, cooldown=60.0):
    return CircuitBreaker("test", failure_threshold=threshold, cooldown_seconds=cooldown,
                           clock=_FakeClock())


# ── State-machine units ─────────────────────────────────────────────────────


def test_closed_opens_after_threshold_consecutive_failures():
    b = _breaker(threshold=5)
    for _ in range(5):
        b.record_failure()
    assert b.state == "open"


def test_failures_below_threshold_stay_closed():
    b = _breaker(threshold=5)
    for _ in range(4):
        b.record_failure()
    assert b.state == "closed"


def test_success_resets_consecutive_failure_counter():
    b = _breaker(threshold=5)
    for _ in range(4):
        b.record_failure()
    b.record_success()
    for _ in range(4):
        b.record_failure()
    assert b.state == "closed"  # 4, not 8, consecutive


def test_open_rejects_calls_immediately():
    b = _breaker(threshold=1)
    b.record_failure()
    assert b.state == "open"
    assert b.allow() is False


def test_open_transitions_to_half_open_after_cooldown():
    b = _breaker(threshold=1, cooldown=30.0)
    b.record_failure()
    assert b.state == "open"
    b._clock.advance(31.0)
    assert b.state == "half_open"


def test_half_open_allows_exactly_one_probe():
    b = _breaker(threshold=1, cooldown=30.0)
    b.record_failure()
    b._clock.advance(31.0)
    assert b.allow() is True   # the probe
    assert b.allow() is False  # second call rejected while probe is in flight


def test_probe_success_closes_circuit():
    b = _breaker(threshold=1, cooldown=30.0)
    b.record_failure()
    b._clock.advance(31.0)
    assert b.allow() is True
    b.record_success()
    assert b.state == "closed"
    assert b.allow() is True


def test_probe_failure_reopens_circuit():
    b = _breaker(threshold=1, cooldown=30.0)
    b.record_failure()
    b._clock.advance(31.0)
    assert b.allow() is True
    b.record_failure()
    assert b.state == "open"
    assert b.allow() is False


# ── LLM wiring test ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_openai_circuit():
    OPENAI_CIRCUIT.reset()
    yield
    OPENAI_CIRCUIT.reset()


def test_llm_circuit_trips_and_fails_fast_in_real_call_path():
    client = MagicMock()
    client.chat.completions.create.side_effect = _connection_error()

    for _ in range(5):
        with pytest.raises(LlmCallError):
            generate_structured(
                system_prompt="s", user_payload="u", json_schema={}, schema_name="n",
                client=client, max_api_retries=0,
            )

    assert OPENAI_CIRCUIT.state == "open"
    calls_so_far = client.chat.completions.create.call_count

    # The 6th call must fail fast WITHOUT another API attempt.
    with pytest.raises(LlmCallError) as exc:
        generate_structured(
            system_prompt="s", user_payload="u", json_schema={}, schema_name="n",
            client=client, max_api_retries=0,
        )
    assert "circuit open" in str(exc.value).lower()
    assert client.chat.completions.create.call_count == calls_so_far


def test_llm_circuit_success_closes_after_failures():
    client = MagicMock()

    # A couple of failures, then success — the success resets the counter.
    client.chat.completions.create.side_effect = _connection_error()
    for _ in range(2):
        with pytest.raises(LlmCallError):
            generate_structured(
                system_prompt="s", user_payload="u", json_schema={}, schema_name="n",
                client=client, max_api_retries=0,
            )

    # Now the provider recovers.
    ok_response = MagicMock()
    ok_response.choices = [MagicMock()]
    ok_response.choices[0].message.refusal = None
    ok_response.choices[0].message.content = '{"contentText": "hi", "evidenceIndexes": [0]}'
    ok_response.usage = None
    ok_response.model = "gpt-4o-mini"
    client.chat.completions.create.side_effect = None
    client.chat.completions.create.return_value = ok_response

    result = generate_structured(
        system_prompt="s", user_payload="u",
        json_schema={"type": "object", "properties": {}, "required": []},
        schema_name="n", client=client, max_api_retries=0,
    )
    assert result.data == {"contentText": "hi", "evidenceIndexes": [0]}
    assert OPENAI_CIRCUIT.state == "closed"
