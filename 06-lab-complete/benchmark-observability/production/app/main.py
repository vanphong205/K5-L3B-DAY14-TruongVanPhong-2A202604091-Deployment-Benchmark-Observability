"""
✅ PRODUCTION — Observable AI Agent

3 trụ cột Observability:
  📈 Metrics → GET /metrics, Prometheus scrape mỗi 5s, Grafana vẽ dashboard
  🔍 Traces  → OpenTelemetry gửi span tới Jaeger (xem request chậm ở bước nào)
  📝 Logs    → JSON có trace_id, nhảy từ 1 dòng log sang đúng trace trong Jaeger

Chạy cả stack: docker compose up --build   (xem README.md)
"""
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response, Security
from fastapi.security.api_key import APIKeyHeader
from opentelemetry.trace import Status, StatusCode
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from app import metrics as m
from app.config import settings
from app.telemetry import current_trace_ids, setup_tracing, shutdown_tracing, tracer
from utils import mock_llm

# ─────────────────────────────────────────────────────────
# Logging — JSON, luôn kèm trace_id để liên kết với Jaeger
# ─────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("agent")


def log_event(event: str, **fields):
    trace_id, span_id = current_trace_ids()
    logger.info(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": "INFO",
        "service": settings.app_name,
        "instance": settings.instance_id,
        "event": event,
        "trace_id": trace_id,
        "span_id": span_id,
        **fields,
    }, ensure_ascii=False))


# ─────────────────────────────────────────────────────────
# App + lifespan
# ─────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    m.AGENT_INFO.labels(version=settings.agent_version, model=settings.llm_model).set(1)
    log_event("startup", agent_version=settings.agent_version, model=settings.llm_model,
              environment=settings.environment)
    yield
    log_event("shutdown")
    shutdown_tracing()


app = FastAPI(title=settings.app_name, version=settings.app_version, lifespan=lifespan)


@app.middleware("http")
async def observe_requests(request: Request, call_next):
    if request.url.path == "/metrics":
        return await call_next(request)

    request_id = request.headers.get("X-Request-ID", uuid.uuid4().hex[:12])
    request.state.request_id = request_id
    start = time.perf_counter()
    m.IN_FLIGHT.inc()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        trace_id, _ = current_trace_ids()
        response.headers["X-Request-ID"] = request_id
        if trace_id:
            response.headers["X-Trace-ID"] = trace_id  # copy ID này để tìm trace trong Jaeger
        return response
    finally:
        m.IN_FLIGHT.dec()
        duration = time.perf_counter() - start
        route = request.scope.get("route")
        route_path = route.path if route else "unmatched"
        m.HTTP_REQUESTS.labels(request.method, route_path, str(status)).inc()
        m.HTTP_LATENCY.labels(request.method, route_path).observe(duration)
        if route_path not in ("/health", "/ready"):
            log_event("http_request", request_id=request_id, method=request.method,
                      route=route_path, status=status, ms=round(duration * 1000, 1))


setup_tracing(app)  # phải gọi trước khi app nhận request

# ─────────────────────────────────────────────────────────
# Auth (tối giản — xem Section 04 cho JWT, rate limit)
# ─────────────────────────────────────────────────────────
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str = Security(api_key_header)):
    if settings.agent_api_key and api_key != settings.agent_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


# ─────────────────────────────────────────────────────────
# Agent logic — mỗi bước là một span
# ─────────────────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)


class Usage(BaseModel):
    input_tokens: int
    output_tokens: int
    cost_usd: float


class AskResponse(BaseModel):
    answer: str
    model: str
    agent_version: str
    usage: Usage
    llm_latency_ms: float
    request_id: str
    trace_id: str


async def run_agent(question: str) -> mock_llm.LLMResult:
    with tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("agent.version", settings.agent_version)
        span.set_attribute("agent.question.length", len(question))

        context = None
        if settings.agent_version == "v2":
            with tracer.start_as_current_span("agent.retrieve_context") as r_span:
                context = await mock_llm.search_knowledge_base(question)
                r_span.set_attribute("retrieval.documents", len(context))
                m.RETRIEVAL_HITS.labels("hit" if context else "miss").inc()

        # Tên span + attribute theo OTel GenAI semantic conventions
        # record_exception=False: tự ghi exception bên dưới (tránh ghi trùng 2 lần)
        with tracer.start_as_current_span(f"chat {settings.llm_model}",
                                          record_exception=False) as llm_span:
            llm_span.set_attribute("gen_ai.operation.name", "chat")
            llm_span.set_attribute("gen_ai.provider.name", "mock")
            llm_span.set_attribute("gen_ai.request.model", settings.llm_model)
            try:
                result = await mock_llm.generate(question, context=context,
                                                 model=settings.llm_model)
            except mock_llm.LLMError as exc:
                llm_span.record_exception(exc)
                llm_span.set_status(Status(StatusCode.ERROR, str(exc)))
                llm_span.set_attribute("error.type", "upstream_unavailable")
                m.LLM_REQUESTS.labels(settings.llm_model, "error").inc()
                raise

            llm_span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
            llm_span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
            llm_span.set_attribute("llm.cost_usd", result.cost_usd)

        m.LLM_REQUESTS.labels(result.model, "ok").inc()
        m.LLM_LATENCY.labels(result.model).observe(result.latency_s)
        m.LLM_TOKENS.labels(result.model, "input").inc(result.input_tokens)
        m.LLM_TOKENS.labels(result.model, "output").inc(result.output_tokens)
        m.LLM_COST.labels(result.model).inc(result.cost_usd)
        return result


# ─────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────
@app.post("/ask", response_model=AskResponse, dependencies=[Depends(verify_api_key)])
async def ask(body: AskRequest, request: Request):
    try:
        result = await run_agent(body.question)
    except mock_llm.LLMError as exc:
        log_event("llm_error", request_id=request.state.request_id, error=str(exc))
        raise HTTPException(status_code=503, detail="LLM upstream unavailable, please retry")

    log_event("llm_call", request_id=request.state.request_id, model=result.model,
              agent_version=settings.agent_version, input_tokens=result.input_tokens,
              output_tokens=result.output_tokens, cost_usd=round(result.cost_usd, 6),
              llm_ms=round(result.latency_s * 1000, 1))

    trace_id, _ = current_trace_ids()
    return AskResponse(
        answer=result.text,
        model=result.model,
        agent_version=settings.agent_version,
        usage=Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    cost_usd=round(result.cost_usd, 8)),
        llm_latency_ms=round(result.latency_s * 1000, 1),
        request_id=request.state.request_id,
        trace_id=trace_id,
    )


@app.get("/health")
def health():
    return {"status": "ok", "version": settings.app_version,
            "agent_version": settings.agent_version, "instance": settings.instance_id}


@app.get("/ready")
def ready():
    return {"ready": True}


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus scrape endpoint. Nginx chặn đường này từ bên ngoài (xem nginx.conf)."""
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=settings.port, access_log=False)
