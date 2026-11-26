"""
Prometheus metrics của agent — một chỗ định nghĩa, cả app dùng chung.

4 Golden Signals (Google SRE book) map sang metric:
  Latency     → http_request_duration_seconds (histogram)
  Traffic     → http_requests_total (counter)
  Errors      → http_requests_total{status=~"5.."}
  Saturation  → agent_in_flight_requests (gauge)

Cộng thêm metric riêng cho LLM: số call, latency, token, cost.

⚠️ Mỗi tổ hợp label = 1 time series. Chỉ dùng label có ít giá trị.
"""
from prometheus_client import Counter, Gauge, Histogram, disable_created_metrics

disable_created_metrics()

LATENCY_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0)

HTTP_REQUESTS = Counter(
    "http_requests_total", "Tổng số HTTP request",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds", "Thời gian xử lý HTTP request",
    ["method", "route"], buckets=LATENCY_BUCKETS,
)
IN_FLIGHT = Gauge(
    "agent_in_flight_requests", "Số request đang xử lý",
)
LLM_REQUESTS = Counter(
    "llm_requests_total", "Số lần gọi LLM",
    ["model", "status"],
)
LLM_LATENCY = Histogram(
    "llm_request_duration_seconds", "Latency của LLM call",
    ["model"], buckets=LATENCY_BUCKETS,
)
LLM_TOKENS = Counter(
    "llm_tokens_total", "Số token đã dùng",
    ["model", "type"],
)
LLM_COST = Counter(
    "llm_cost_usd_total", "Tổng chi phí LLM (USD)",
    ["model"],
)
RETRIEVAL_HITS = Counter(
    "agent_retrieval_total", "Kết quả bước retrieval của agent",
    ["result"],  # hit | miss
)
AGENT_INFO = Gauge(
    "agent_info", "Thông tin phiên bản agent (luôn = 1)",
    ["version", "model"],
)
