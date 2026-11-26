"""
Cost guard (tiêu chí 8).
"""
import time
from fastapi import HTTPException
from app.config import settings
from app.redis_client import r

PRICE_INPUT_PER_1K = 0.00015
PRICE_OUTPUT_PER_1K = 0.0006


def check_budget() -> None:
    """Gọi TRƯỚC khi gọi LLM. Hết budget → raise HTTPException(status_code=402)."""
    try:
        day_str = time.strftime("%Y-%m-%d")
        redis_key = f"cost:{day_str}"
        current_cost = float(r.get(redis_key) or 0.0)
        if current_cost >= settings.daily_budget_usd:
            raise HTTPException(
                status_code=402,
                detail=f"Daily budget of ${settings.daily_budget_usd:.4f} exceeded.",
            )
    except HTTPException:
        raise
    except Exception:
        pass


def record_cost(input_tokens: int, output_tokens: int) -> float:
    """Gọi SAU khi gọi LLM. Cộng chi phí của request vào tổng, trả về chi phí request."""
    request_cost = (input_tokens / 1000.0) * PRICE_INPUT_PER_1K + (output_tokens / 1000.0) * PRICE_OUTPUT_PER_1K
    try:
        day_str = time.strftime("%Y-%m-%d")
        redis_key = f"cost:{day_str}"
        r.incrbyfloat(redis_key, request_cost)
        r.expire(redis_key, 86400 * 2)
    except Exception:
        pass
    return request_cost
