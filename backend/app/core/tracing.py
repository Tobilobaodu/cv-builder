"""OpenTelemetry tracing — spans around the two LLM call sites that
matter most (generate_structured/stream_text in app/services/llm_client.py),
so a cost or latency spike can be attributed to a specific prompt version
without guessing. Span attributes carry prompt_version/model/input_tokens/
output_tokens/cost_usd — all data that already exists on
StructuredGenerationResult and the calling prompt modules, this module
just wires it onto a span.

No collector/backend is wired up here — Jaeger/Tempo/Honeycomb/etc. is a
separate decision once one is chosen, same as Alertmanager's real
receiver. For now spans export to this process's own console/logs, which
is enough to confirm the mechanism works locally (change a prompt
version, rerun, see it in the span output). Swapping in a real OTLP
exporter later is a one-line change here — add an OTLPSpanExporter
alongside/instead of ConsoleSpanExporter — that doesn't touch either call
site in llm_client.py.

SimpleSpanProcessor, not BatchSpanProcessor: this exports synchronously,
so a span shows up in the logs the moment its `with` block exits rather
than on a several-second batch delay — the right tradeoff for a console
exporter used for local verification, not for a real network exporter
under load.
"""

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from app.core.config import settings

_provider = TracerProvider(resource=Resource.create({"service.name": settings.app_name}))
_provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
trace.set_tracer_provider(_provider)

tracer = trace.get_tracer("jbs.llm_client")
