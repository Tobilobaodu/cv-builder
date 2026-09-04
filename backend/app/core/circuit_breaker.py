"""Small closed/open/half-open circuit breaker for external calls.

Per security-plan §6: if Textract or the LLM provider is failing or degraded,
fail fast rather than queuing requests that will only time out later and hold
worker capacity.

Deliberately hand-rolled rather than pulling in a library: a closed/open/
half-open state machine is small enough that a hand-written version is easier
for the next developer to reason about, and this project has already hit the
"a Celery task exists but isn't wired to a queue" class of bug — the wiring
test in test_circuit_breaker.py exists specifically to prevent that same
failure mode here.

State semantics:
    closed     — calls pass; ``failure_threshold`` consecutive failures open it.
    open       — calls are rejected immediately (CircuitOpenError), no attempt.
    half_open  — after ``cooldown_seconds``, exactly one probe call is allowed
                 through; success closes the circuit, failure re-opens it.
"""

from __future__ import annotations

import threading
import time


class CircuitOpenError(Exception):
    """Raised (or mapped) when a call is rejected because its circuit is open."""


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        clock=None,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._state = "closed"  # closed | open | half_open
        self._opened_at = 0.0
        self._probe_in_flight = False

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_transition()
            return self._state

    def _maybe_transition(self) -> None:
        """Cooldown expiry moves open -> half_open (a probe may pass through)."""
        if self._state == "open" and (self._clock() - self._opened_at) >= self.cooldown_seconds:
            self._state = "half_open"
            self._probe_in_flight = False

    def allow(self) -> bool:
        """Return True if the call may proceed, False if the circuit rejects it."""
        with self._lock:
            self._maybe_transition()
            if self._state == "open":
                return False
            if self._state == "half_open":
                if self._probe_in_flight:
                    return False
                self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            self._probe_in_flight = False
            self._state = "closed"

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            self._probe_in_flight = False
            if self._state == "half_open":
                # A probe failure immediately re-opens — never linger half-open.
                self._state = "open"
                self._opened_at = self._clock()
            elif self._consecutive_failures >= self.failure_threshold:
                self._state = "open"
                self._opened_at = self._clock()

    def reset(self) -> None:
        """Return to a freshly-closed state (used by tests between cases)."""
        with self._lock:
            self._consecutive_failures = 0
            self._state = "closed"
            self._probe_in_flight = False
            self._opened_at = 0.0


# Process-wide named circuits, one per external dependency. Workers import
# these singletons; tests construct their own CircuitBreaker instances with a
# fake clock so no test ever sleeps through a real cooldown window.
TEXTRACT_CIRCUIT = CircuitBreaker("textract")
OPENAI_CIRCUIT = CircuitBreaker("openai")
