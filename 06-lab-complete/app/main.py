"""
Production AI Agent — Part 6 Final Project
"""
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.auth import verify_api_key
from app.rate_limiter import check_rate_limit
from app.cost_guard import check_budget, record_cost
from app.redis_client import redis_healthy
from utils.mock_llm import ask as llm_ask


class JsonFormatter(logging.Formatter):
    """Mỗi dòng log của app là 1 JSON object hợp lệ."""
    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "lvl": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "props"):
            log_obj.update(record.props)
        return json.dumps(log_obj, ensure_ascii=False)


# Cấu hình logging
handler = logging.StreamHandler()
handler.setFormatter(JsonFormatter())
logging.root.handlers = [handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Application starting up")
    yield
    # Shutdown
    logger.info("Application shutting down gracefully")


app = FastAPI(title="Production AI Agent", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Trả JSON 500 gọn, tránh lộ stack trace ra client."""
    logger.error(
        "Unhandled exception",
        extra={"props": {"event": "error", "error_type": type(exc).__name__}},
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    user_id: Optional[str] = None


@app.get("/health")
def health():
    """Liveness probe: luôn trả 200 khi process còn chạy."""
    return {"status": "ok"}


@app.get("/ready")
def ready():
    """Readiness probe: 200 khi Redis kết nối được, 503 khi lỗi."""
    if not redis_healthy():
        raise HTTPException(status_code=503, detail="Redis connection failed")
    return {"status": "ready"}


@app.post("/ask")
def ask(body: AskRequest, api_key: str = Depends(verify_api_key)):
    start_time = time.time()

    # Rate limiting
    check_rate_limit(api_key)

    # Cost guard
    check_budget()

    # LLM execution
    answer = llm_ask(body.question)

    # Simple token estimation & recording
    input_tokens = len(body.question.split())
    output_tokens = len(answer.split())
    cost = record_cost(input_tokens, output_tokens)

    elapsed_ms = round((time.time() - start_time) * 1000, 2)
    logger.info(
        f"Processed ask request in {elapsed_ms}ms",
        extra={"props": {"event": "ask", "elapsed_ms": elapsed_ms, "cost": cost}}
    )

    return {
        "question": body.question,
        "answer": answer,
        "user_id": body.user_id,
    }
