"""
Rate limiting (tiêu chí 7).
"""
import time
from fastapi import HTTPException
from app.config import settings
from app.redis_client import r


def check_rate_limit(key: str) -> None:
    """
    Sliding window 60s using Redis Sorted Set.
    Raises HTTPException(429) if exceeded.
    """
    now = time.time()
    window_start = now - 60.0
    redis_key = f"rate_limit:{key}"

    try:
        # 1. Remove old timestamps outside sliding window
        r.zremrangebyscore(redis_key, 0, window_start)

        # 2. Count requests in current window
        current_count = r.zcard(redis_key)
        if current_count >= settings.rate_limit_per_minute:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Max {settings.rate_limit_per_minute} requests per minute.",
                headers={"Retry-After": "60"},
            )

        # 3. Add current request timestamp
        r.zadd(redis_key, {str(now): now})
        r.expire(redis_key, 60)
    except HTTPException:
        raise
    except Exception:
        # Fallback if redis temporarily fails in non-strict modes
        pass
