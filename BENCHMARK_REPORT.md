# Benchmark & Observability Report — Day 14

**Họ tên:** Trương Văn Phong  
**MSSV:** 2A202604091  
**Lớp:** K5-L3B  
**Ngày chạy:** 2026-11-26  
**Môi trường:** Windows 11 x64, Python 3.13 / WSL2 Linux 6.6.87, Docker Desktop (WSL2 Engine), 8 Cores CPU, 16GB RAM  

---

## 1. SLO của agent

| SLI | SLO | Lý do chọn ngưỡng này |
|---|---|---|
| p95 latency `/ask` | < 500 ms | Đảm bảo trải nghiệm người dùng mượt mà, phản hồi kịp thời trong tương tác đối thoại, tránh nghẽn hàng đợi khi chịu tải vừa. |
| Error rate `/ask` | < 2.0 % | Cho phép dung sai đối với các sự cố mạng và timeout upstream LLM ngẫu nhiên nhưng vẫn duy trì độ tin cậy dịch vụ cao. |
| Accuracy trên golden dataset | ≥ 80.0 % | Đảm bảo kiến thức cốt lõi của agent được cung cấp chính xác, bắt buộc vượt qua quality gate trước khi deploy lên production. |

---

## 2. Benchmark hiệu năng (develop, `load_test.py`)

### 2.1 Concurrency vs latency (agent v2)
*(Dữ liệu đo đạc thực tế từ các file `reports/load_v2_c1.json`, `c10.json`, `c50.json`, `c100.json`, mỗi mức 300 requests)*

| Concurrency | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Error rate | SLO |
|---|---|---|---|---|---|---|
| 1 | 3.61 | 217.1 | 291.1 | 1724.2 | 0.0% | PASS |
| 10 | 29.54 | 224.7 | 568.9 | 1733.9 | 0.0% | FAIL (p95 > 500ms) |
| 50 | 53.49 | 484.7 | 2698.3 | 3496.6 | 0.0% | FAIL (p95 > 500ms) |
| 100 | 36.86 | 1277.3 | 5788.2 | 6095.0 | 0.0% | FAIL (p95 > 500ms) |

**Nhận xét:** RPS có tăng tuyến tính theo concurrency không? Vì sao?
- **Không.** RPS chỉ tăng mạnh khi concurrency tăng từ 1 lên 10 (từ 3.61 lên 29.54 req/s) và đạt đỉnh ở mức concurrency 50 (~53.49 req/s). Khi concurrency tiếp tục tăng lên 100, RPS bắt đầu suy giảm còn 36.86 req/s.
- **Nguyên nhân:** Khi concurrency quá cao, hệ thống gặp nút thắt tài nguyên (CPU context-switching, tranh chấp hàng đợi event loop của FastAPI/Uvicorn, overhead xử lý async I/O và tail latency tích lũy), khiến thời gian chờ của mỗi request kéo dài, đẩy latency p50 lên 1277.3ms và p95 lên 5788.2ms, làm giảm thông lượng tổng thể.

### 2.2 Tail latency
*(Dữ liệu đo đạc thực tế từ các file `reports/load_v2_slow_0.json`, `load_v2_slow_0.03.json`, `load_v2_slow_0.1.json`, mỗi mức 300 requests, concurrency 20)*

| `MOCK_SLOW_RATE` | avg (ms) | p95 (ms) | p99 (ms) |
|---|---|---|---|
| 0 | 220.1 | 461.1 | 506.8 |
| 0.03 (mặc định) | 261.9 | 487.0 | 1684.8 |
| 0.1 | 350.9 | 1704.4 | 1759.4 |

**Nhận xét:** Chỉ số nào nhạy nhất với request chậm? Vì sao SLO không nên đặt theo average?
- **Chỉ số nhạy nhất:** **p95 và p99**. Khi `MOCK_SLOW_RATE` tăng từ 0.03 lên 0.1, chỉ số `avg` chỉ tăng từ 261.9ms lên 350.9ms (tăng ~1.34 lần), trong khi `p95` tăng vọt từ 487.0ms lên 1704.4ms (tăng ~3.5 lần).
- **Lý do không đặt SLO theo average:** Trung bình số học (average) bị làm loãng bởi số lượng lớn các request nhanh, làm che giấu hoàn toàn các request bị nghẽn (tail latency). Đặt SLO theo percentile (p95/p99) giúp phản ánh chính xác trải nghiệm thực tế của người dùng ở nhóm chịu ảnh hưởng xấu nhất.

---

## 3. Benchmark chất lượng (develop, `eval/run_eval.py`)
*(Dữ liệu đo đạc thực tế từ `reports/eval_v1.json`, `reports/eval_v2.json`, `reports/eval_v2_fixed.json` trên 23 câu hỏi golden dataset)*

| Chỉ số | Agent v1 | Agent v2 (ban đầu) | Agent v2 (sau khi sửa KB) |
|---|---|---|---|
| Accuracy | 4.3% (1/23) | 100.0% (23/23) | 100.0% (23/23) |
| Avg tokens / câu | 56.2 | 113.7 | 113.7 |
| Tổng cost (USD) | $0.000391 | $0.000790 | $0.000790 |
| Cost / câu đúng (USD) | $0.00039135 | $0.00003436 | $0.00003436 |
| Quality gate | FAIL | PASS | PASS (23/23, 0 regression) |

**Những câu v2 trả lời sai ban đầu và cách bạn sửa:**

| ID | Keyword bị thiếu | Bạn đã sửa gì |
|---|---|---|
| `scaling-03` | `cpu`, `memory`, `custom metrics` | Thêm tài liệu về HPA vào `KNOWLEDGE_BASE`: `"Kubernetes HPA (Horizontal Pod Autoscaler) scale số pod dựa trên CPU, memory hoặc custom metrics như RPS hay độ dài hàng đợi."` |
| `security-02` | `token`, `hết hạn`, `chữ ký` | Thêm tài liệu phân biệt API Key và JWT vào `KNOWLEDGE_BASE`: `"API key là chuỗi bí mật cố định, không tự hết hạn. JWT là token có chữ ký số, chứa thông tin user và thời điểm hết hạn; server xác minh chữ ký mà không cần tra database."` |

**Nhận xét về trade-off chất lượng / chi phí / latency giữa v1 và v2:**
- Agent v1 không có Retrieval (RAG) nên câu trả lời chung chung, độ chính xác rất thấp (4.3%). Dù latency thấp hơn (~164ms p50) và cost tổng ít hơn ($0.000391), nhưng "chi phí cho mỗi câu trả lời ĐÚNG" lại rất đắt ($0.00039135/câu đúng).
- Agent v2 bổ sung cơ chế RAG giúp accuracy đạt tuyệt đối 100% (23/23). Mặc dù số lượng token trung bình tăng gấp đôi (113.7 vs 56.2) và latency tăng thêm ~50ms (do bước search knowledge base), chi phí thực tế cho mỗi câu trả lời đúng giảm tới hơn **11 lần** ($0.00003436 vs $0.00039135), mang lại hiệu quả vượt trội.

---

## 4. Observability stack (production)

### 4.1 Screenshot
- [x] Dashboard Grafana khi đang có tải (thấy rõ Golden Signals): `screenshots/grafana-dashboard.png`
- [x] Một trace chậm trong Jaeger (thấy span nào chiếm thời gian): `screenshots/jaeger-slow-trace.png`
- [x] Một trace lỗi trong Jaeger (có event `exception`): `screenshots/jaeger-error-trace.png`
- [x] Trang Alerts của Prometheus với ít nhất 1 alert đang **firing**: `screenshots/prometheus-alert-firing.png`

### 4.2 Thí nghiệm "phá hệ thống"

| # | Thay đổi | Alert nào firing | Thời gian từ lúc đổi → firing | Panel nào phản ánh rõ nhất |
|---|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | `HighErrorRate` | ~45 giây | Error Rate (%) |
| B | `MOCK_SLOW_RATE=0.3` | `HighLatencyP95` | ~60 giây | p95 Latency (ms) |
| C | `LLM_MODEL=mock-llm-large` | `HighCostRate` / `HighLatencyP95` | ~90 giây | Token Usage & Estimated Cost / Latency |
| D | `docker pause` 1 instance | `InstanceDown` / `HighErrorRate` | ~30 giây | Instances Up / Request Throughput |
| E | `AGENT_VERSION=v1` | Không có alert nào firing | Không firing | (Chỉ thấy giảm token/request) |

**Thí nghiệm E không làm alert nào firing. Bạn sẽ phát hiện loại sự cố này trong production bằng cách nào?**
- Sự cố giảm chất lượng phản hồi (semantic degradation / hallucination) không gây lỗi HTTP 5xx hay tăng latency nên Prometheus không thể phát hiện qua infrastructure metrics thông thường.
- Để phát hiện trong production, cần thiết lập:
  1. **Continuous Evaluation Pipeline:** Định kỳ (vd mỗi 15 phút hoặc sau mỗi commit/deploy) chạy bộ eval golden dataset tự động trên môi trường live.
  2. **LLM-as-a-Judge & User Feedback Observability:** Theo dõi tỷ lệ người dùng bấm nút Dislike / Thumbs-down, tỷ lệ hủy phiên hội thoại sớm, hoặc sử dụng một model evaluator (như GPT-4o-mini) đánh giá ngẫu nhiên 1-5% traffic thật qua Langfuse / Arize Phoenix.

### 4.3 Debug bằng trace

- `trace_id`: `4e16f838c3d7`
- Tổng thời gian: `1728.4 ms`
- Span chiếm nhiều thời gian nhất: `llm_generate` (`1692.8 ms`, chiếm `97.9%` tổng thời gian)
- Dòng log tương ứng (grep theo `trace_id`):
```json
{"ts": "2026-11-26T08:29:45.285907+00:00", "event": "llm_call", "request_id": "f3acc9a23481", "trace_id": "4e16f838c3d7", "model": "mock-llm-small", "agent_version": "v2", "input_tokens": 78, "output_tokens": 39, "cost_usd": 0.000035, "llm_ms": 1692.8, "retrieved": true}
```

---

## 5. Benchmark scaling (production, Locust headless, 100 users, 2 phút)

| Số instance | RPS | p50 (ms) | p95 (ms) | p99 (ms) | Failures |
|---|---|---|---|---|---|
| 1 instance | 27.0 req/s | 185 ms | 310 ms | 1720 ms | 0 (0.0%) |
| 3 instances | 79.0 req/s | 92 ms | 178 ms | 1620 ms | 0 (0.0%) |

**Giải thích kết quả:** Scale có cải thiện p95 không? Nút thắt nằm ở đâu?
- **Scale có cải thiện đáng kể p95:** p95 giảm từ 310ms xuống còn 178ms, đồng thời thông lượng RPS tăng gần gấp 3 lần (từ 27.0 lên 79.0 req/s).
- **Nút thắt nằm ở đâu:**
  - Đối với các request thông thường, nút thắt nằm ở khả năng concurrency của single worker process trên 1 instance container. Khi scale lên 3 instances, Nginx load balancer phân tán đều tải giúp giảm thời gian xếp hàng của request.
  - Tuy nhiên, chỉ số `p99` vẫn ở mức ~1620ms do sự hiện diện của **upstream tail latency** (`MOCK_SLOW_RATE`), đây là nút thắt từ nhà cung cấp LLM bên ngoài mà việc scale ngang frontend compute không thể giải quyết triệt để.

---

## 6. Kết luận

- **3 điều học được về benchmark & observability:**
  1. *Đừng tin vào số trung bình (average):* Percentile (p95, p99) là thước đo trung thực duy nhất cho trải nghiệm người dùng và tail latency.
  2. *Stateless là điều kiện tiên quyết để scale:* Không có Redis tập trung thì load balancer sẽ làm vỡ ngữ cảnh hội thoại và rate limiting.
  3. *Observability là kiềng 3 chân (Metrics, Traces, Logs):* Metrics cho biết hệ thống đang đau ở đâu; Traces chỉ ra đích xác span nào gây nghẽn; Logs JSON cung cấp ngữ cảnh chi tiết để tái hiện lỗi.
- **Nếu đưa agent này lên production thật, sẽ thêm:**
  - Semantic Caching (Redis/Qdrant) để cache câu trả lời cho các câu hỏi phổ biến, giảm 60-80% chi phí LLM.
  - LLM Fallback & Circuit Breaker (tự động chuyển đổi giữa OpenAI/Anthropic/Gemini khi một bên gặp sự cố).
  - Alertmanager tích hợp Slack / PagerDuty webhook để thông báo on-call khi error budget cạn kiệt.
