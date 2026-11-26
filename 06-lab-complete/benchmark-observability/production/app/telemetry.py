"""
OpenTelemetry tracing setup.

Luồng dữ liệu:
  FastAPI app ──(OTLP/HTTP :4318)──▶ Jaeger ──▶ Jaeger UI :16686

Mỗi request /ask tạo ra một trace gồm các span:

  POST /ask                         ← tự động (FastAPIInstrumentor)
  └── agent.run                     ← tự viết
      ├── agent.retrieve_context    ← tự viết (chỉ v2)
      └── chat mock-llm-small       ← tự viết, theo OTel GenAI semantic conventions

Nhìn trace sẽ thấy ngay request chậm vì bước nào.
"""
import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import settings

logger = logging.getLogger(__name__)


def setup_tracing(app) -> None:
    if not settings.tracing_enabled:
        logger.info('{"event":"tracing_disabled"}')
        return

    resource = Resource.create({
        "service.name": settings.app_name,
        "service.version": settings.app_version,
        "service.instance.id": settings.instance_id,
        "deployment.environment.name": settings.environment,
        "agent.version": settings.agent_version,
    })
    provider = TracerProvider(resource=resource)
    # BatchSpanProcessor gửi span theo lô ở background → không làm chậm request.
    # OTLPSpanExporter() tự đọc OTEL_EXPORTER_OTLP_ENDPOINT và thêm /v1/traces.
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    # Không trace các endpoint vận hành → trace chỉ chứa traffic thật.
    # exclude_spans: bỏ các span nội bộ của ASGI (receive/send) cho trace gọn.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics",
                                       exclude_spans=["receive", "send"])
    logger.info('{"event":"tracing_enabled","endpoint":"%s"}', settings.otlp_endpoint)


def shutdown_tracing() -> None:
    provider = trace.get_tracer_provider()
    if hasattr(provider, "shutdown"):
        provider.shutdown()  # flush các span còn trong buffer trước khi tắt


def current_trace_ids() -> tuple[str, str]:
    """trace_id / span_id hiện tại (hex) — gắn vào log để nhảy từ log sang trace."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return "", ""
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")


tracer = trace.get_tracer("agent")
