"""Application-level OpenTelemetry tracing for the copilot, exported to Phoenix.

This traces the copilot *application* (tool calls, retrieval, the request as a
whole) under its own Phoenix project, PHOENIX_APP_PROJECT_NAME, distinct from
the gateway's own LLM-call traces (PHOENIX_PROJECT_NAME, gateway/config.yaml,
B10). Both point at the same self-hosted Phoenix instance (B9), but the
gateway sends its spans over gRPC from inside the Docker Compose network; this
module sends over OTLP/HTTP to Phoenix's host-mapped port, since the copilot
process runs on the host, not in a container -- `phoenix:4317` (the gateway's
compose-internal address) would not resolve here.

Tracing must never break the copilot: if Phoenix is unreachable, at startup or
per-request, every call in this module still returns normally. Setup failures
fall back to OpenTelemetry's own no-op tracer, and BatchSpanProcessor already
swallows exporter errors internally (the same "log, never raise" behavior
confirmed for the gateway's own traces in B10).

Usage:
    from copilot.tracing import trace_span

    with trace_span("get_order", order_no=order_no):
        ...
"""

import logging
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Span, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from common.settings import SettingsError, load_settings

logger = logging.getLogger(__name__)

# OTel span attribute values must be one of these types (or a homogeneous
# sequence of them); anything else needs converting before set_attribute.
_ATTRIBUTE_TYPES = (bool, int, float, str)


def _build_tracer() -> trace.Tracer:
    """Build the real tracer, or OTel's own no-op one if anything goes wrong."""
    try:
        settings = load_settings()
        # config/copilot.yaml is read the same way copilot/api.py does; a
        # missing or invalid file falls through to the except below.
        import yaml

        from copilot.rag.config import REPO_ROOT

        cfg = yaml.safe_load(
            (REPO_ROOT / "config" / "copilot.yaml").read_text(encoding="utf-8")
        )
        sample_rate = float(cfg["trace_sample_rate"])

        resource = Resource.create(
            {
                SERVICE_NAME: "shopease-copilot-app",
                "openinference.project.name": settings.phoenix_app_project_name,
            }
        )
        provider = TracerProvider(
            resource=resource, sampler=ParentBased(TraceIdRatioBased(sample_rate))
        )
        # A plain http:// endpoint is plaintext with no separate insecure flag
        # to set (unlike the gateway's gRPC exporter in B10, which defaulted to
        # TLS against Phoenix's plaintext collector until fixed explicitly).
        endpoint = (
            f"http://{settings.copilot_api_host}:{settings.phoenix_port}/v1/traces"
        )
        # copilot_api_host is 127.0.0.1 by default (same host Phoenix's ports
        # are mapped to, both bound to 127.0.0.1 in docker-compose.yml), so it
        # doubles as "the host running this process" without a new setting.
        exporter = OTLPSpanExporter(endpoint=endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        return provider.get_tracer("shopease.copilot")
    except Exception:
        logger.warning(
            "Tracing disabled: could not set up the Phoenix exporter "
            "(is docker compose --profile obs up -d phoenix running?)",
            exc_info=True,
        )
        return trace.NoOpTracer()


_tracer = _build_tracer()


def _set_attribute(span: Span, key: str, value: object) -> None:
    is_homogeneous_sequence = isinstance(value, (list, tuple)) and all(
        isinstance(v, _ATTRIBUTE_TYPES) for v in value
    )
    if isinstance(value, _ATTRIBUTE_TYPES) or is_homogeneous_sequence:
        span.set_attribute(key, value)
    else:
        span.set_attribute(key, repr(value))


@contextmanager
def trace_span(name: str, **attributes: Any) -> Generator[Span, None, None]:
    """Create a span named `name` with the given attributes.

    Never raises: with tracing disabled or misconfigured, this is a silent
    no-op and the wrapped code runs exactly as if the `with` block were not
    there. Exceptions raised *inside* the `with` block still propagate
    normally -- only the tracing machinery itself is made safe.
    """
    try:
        with _tracer.start_as_current_span(name) as span:
            for key, value in attributes.items():
                _set_attribute(span, key, value)
            yield span
    except Exception:
        logger.debug(
            "Tracing error for span %r; continuing untraced", name, exc_info=True
        )
        yield trace.INVALID_SPAN


def set_span_attributes(span: Span, attributes: Mapping[str, object]) -> None:
    """Add attributes to an already-open span, e.g. a result computed inside the block."""
    try:
        for key, value in attributes.items():
            _set_attribute(span, key, value)
    except Exception:
        logger.debug(
            "Failed to set span attributes; continuing untraced", exc_info=True
        )


if __name__ == "__main__":
    try:
        load_settings()
    except SettingsError as exc:
        raise SystemExit(str(exc)) from None
    with trace_span("manual-test", source="python -m copilot.tracing") as span:
        set_span_attributes(span, {"ok": True})
    print("no exception raised")
