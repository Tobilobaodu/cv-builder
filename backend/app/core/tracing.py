"""OpenTelemetry tracing — spans around the two LLM call sites that
matter most (generate_structured/stream_text in app/services/llm_client.py),
so a cost or latency spike can be attributed to a specific prompt version
without guessing. Span attributes carry prompt_version/model/input_tokens/
output_tokens/cost_usd — all data that already exists on
StructuredGenerationResult and the calling prompt modules, this module
just wires it onto a span.

Export target is chosen from the environment rather than configured here.
A Temps deployment arrives with OTEL_EXPORTER_OTLP_ENDPOINT, _HEADERS and
_PROTOCOL already set, so spans go to the platform with nothing to
configure; anywhere those are absent (local runs, pytest) the exporter
falls back to the console, which is what this module did exclusively
before. That keeps local verification unchanged — change a prompt
version, rerun, see it in the span output — while a deployed process
sends the same spans somewhere durable.

_HEADERS carries a live deployment token. It is read by the exporter
straight from the environment and deliberately never logged or echoed.

Processor choice follows the exporter. The console path keeps
SimpleSpanProcessor so a span appears the moment its `with` block exits,
which is the point of a console exporter. The OTLP path uses
BatchSpanProcessor: exporting synchronously over the network would put
an HTTP round trip on the critical path of every traced LLM call.
"""

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)

from app.core.config import settings

# OTEL_SERVICE_NAME wins when the platform sets it, so traces from this
# process group under the same service identity Temps shows elsewhere.
_service_name = os.getenv("OTEL_SERVICE_NAME") or settings.app_name

_provider = TracerProvider(resource=Resource.create({"service.name": _service_name}))

if settings.otel_traces_enabled and os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
    # Imported lazily: the OTLP exporter pulls in protobuf and requests, and
    # nothing should pay that import cost on a local run that will not use it.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    _provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
else:
    _provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

trace.set_tracer_provider(_provider)

tracer = trace.get_tracer("jbs.llm_client")


def shutdown_tracing() -> None:
    """Flush pending spans and stop the exporter.

    BatchSpanProcessor holds spans in memory between exports, so without an
    explicit flush a container stopping inside Temps' ten-second grace period
    loses whatever had not been sent — which is exactly the window where the
    spans explaining a shutdown are most wanted.
    """
    _provider.shutdown()
