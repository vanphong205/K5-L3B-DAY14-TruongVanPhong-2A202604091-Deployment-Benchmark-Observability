"""
Kết nối Redis dùng chung cho rate limiter và cost guard.
"""
import redis
from app.config import settings

# Lazy connection - don't connect immediately on import
r = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)


def redis_healthy() -> bool:
    """Check if Redis connection is working."""
    try:
        return bool(r.ping())
    except Exception:
        return False
