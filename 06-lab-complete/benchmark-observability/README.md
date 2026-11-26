# Part 7 — Benchmark & Observability

Deploy lên cloud xong mới là **bắt đầu**. Từ lúc có người dùng thật, bạn phải trả lời được 3 câu hỏi:

1. **Agent có đủ nhanh và ổn định không?** → *Benchmark hiệu năng* (load test)
2. **Agent có trả lời đúng không — và bản mới có tệ hơn bản cũ không?** → *Benchmark chất lượng* (eval)
3. **Ngay lúc này agent có đang khoẻ không? Nếu chậm thì chậm ở đâu?** → *Observability* (metrics, logs, traces)

Section này dạy cả ba, dùng mock LLM nên **chạy hoàn toàn offline, không cần API key**.

> Thư mục này nằm trong `06-lab-complete/` vì Part 7 nối tiếp Final Project: sau khi build xong agent production, bạn đo và giám sát nó. Agent ở đây là bản rút gọn của agent Part 6 (giữ API key, bỏ rate limit và cost guard để load test không bị chặn 429), có thêm metrics và tracing. Code chạy độc lập, không phụ thuộc file nào khác trong `06-lab-complete/`.

---

## Mục tiêu

Sau section này bạn có thể:

- [ ] Giải thích vì sao dùng **p95/p99** thay cho latency trung bình
- [ ] Viết **SLO** cho agent và kiểm tra bằng load test
- [ ] Chạy **eval trên golden dataset**, so sánh 2 phiên bản agent, chặn deploy khi có regression
- [ ] Instrument agent với **Prometheus metrics** (counter, gauge, histogram) đúng cách
- [ ] Đọc **dashboard Grafana** theo 4 Golden Signals
- [ ] Dùng **OpenTelemetry + Jaeger** để tìm bước làm request chậm
- [ ] Viết **alert rule** và unit test cho nó

---

## Khái niệm

### Benchmark hiệu năng vs Benchmark chất lượng

| | Hiệu năng (load test) | Chất lượng (eval) |
|---|---|---|
| Câu hỏi | Nhanh không? Chịu được bao nhiêu tải? | Trả lời đúng không? |
| Đo gì | RPS, latency p50/p95/p99, error rate | Accuracy, score theo category, cost/câu đúng |
| Input | Nhiều request song song | Golden dataset (câu hỏi + đáp án mong đợi) |
| Công cụ trong lab | `load_test.py`, Locust | `eval/run_eval.py` |
| Khi nào chạy | Trước release, sau khi đổi hạ tầng | Mỗi lần đổi prompt/model/retrieval (trong CI) |

> ⚠️ Eval với 23 câu hỏi **không** đủ để kết luận về latency — mẫu quá nhỏ, 1 request chậm là p95 nhảy vọt. Latency phải đo bằng load test.

### Vì sao không nhìn latency trung bình?

```
100 request:  97 cái mất 150ms   +   3 cái mất 1.700ms

  average = 197ms   ← trông ổn
  p50     = 150ms
  p95     = 150ms
  p99     = 1.700ms ← cứ 100 người thì 1 người chờ gần 2 giây
```

Mock LLM trong lab cố tình cho ~3% request chậm bất thường (`MOCK_SLOW_RATE`) để bạn **thấy** điều này trên report và dashboard.

### SLI, SLO, Error budget

| Khái niệm | Định nghĩa | Ví dụ trong lab |
|---|---|---|
| **SLI** (indicator) | Chỉ số đo được | Tỉ lệ request `/ask` trả 2xx; p95 latency |
| **SLO** (objective) | Mục tiêu cho SLI | p95 < 500ms, error rate < 2% |
| **Error budget** | Phần được phép hỏng = 100% − SLO | SLO 98% thành công → được lỗi 2% |
| **Alert** | Báo động khi vi phạm *rõ rệt và kéo dài* | error rate > 5% trong 1 phút |

Ngưỡng alert lỏng hơn SLO một chút để tránh báo động liên tục vì spike ngắn (alert fatigue).

### 3 trụ cột Observability

| Trụ cột | Trả lời câu hỏi | Trong lab |
|---|---|---|
| 📈 **Metrics** | *Có vấn đề không?* (số liệu tổng hợp theo thời gian) | Prometheus scrape `/metrics` → Grafana |
| 🔍 **Traces** | *Vấn đề ở bước nào?* (đường đi của 1 request) | OpenTelemetry → Jaeger |
| 📝 **Logs** | *Chuyện gì đã xảy ra?* (chi tiết từng sự kiện) | JSON log có `trace_id` |

Ba trụ cột liên kết với nhau: dashboard báo p95 tăng → mở Jaeger tìm trace chậm → lấy `trace_id` grep log để xem chi tiết.

### 4 Golden Signals → metric của agent

| Signal | Metric | Loại |
|---|---|---|
| **Latency** | `http_request_duration_seconds` | Histogram |
| **Traffic** | `http_requests_total` | Counter |
| **Errors** | `http_requests_total{status=~"5.."}` | Counter |
| **Saturation** | `agent_in_flight_requests` | Gauge |
| *LLM riêng* | `llm_request_duration_seconds`, `llm_tokens_total`, `llm_cost_usd_total` | Histogram, Counter |

**Counter** chỉ tăng (dùng với `rate()`), **Gauge** lên xuống, **Histogram** chia giá trị vào các bucket để tính percentile bằng `histogram_quantile()`.

> ⚠️ **Cardinality:** mỗi tổ hợp giá trị label là 1 time series. Label `route="/ask"` → vài series. Label `user_id` hay `question` → hàng triệu series, Prometheus hết RAM. Chỉ dùng label có ít giá trị.

---

## Cấu trúc

```
06-lab-complete/benchmark-observability/
├── README.md
├── BENCHMARK_REPORT_TEMPLATE.md     # Mẫu báo cáo benchmark (nộp bài)
├── develop/                         # Bước 1: chạy local, không cần Docker
│   ├── app.py                       #   Agent có /metrics + JSON log
│   ├── load_test.py                 #   Benchmark hiệu năng (p50/p95/p99, RPS, SLO)
│   ├── eval/
│   │   ├── golden_dataset.jsonl     #   23 câu hỏi + keyword mong đợi
│   │   └── run_eval.py              #   Benchmark chất lượng + quality gate
│   ├── requirements.txt
│   └── utils/mock_llm.py            #   Mock LLM: latency, lỗi, token, cost, knowledge base
└── production/                      # Bước 2: full stack bằng Docker Compose
    ├── docker-compose.yml           #   agent x2 + nginx + prometheus + grafana + jaeger + locust
    ├── Dockerfile
    ├── .env.example                 #   Các "núm vặn" để đổi version/model/fault injection
    ├── app/
    │   ├── main.py                  #   Agent: metrics + traces + log có trace_id
    │   ├── metrics.py               #   Định nghĩa Prometheus metrics
    │   ├── telemetry.py             #   OpenTelemetry setup
    │   └── config.py
    ├── nginx/nginx.conf             #   Load balancer, chặn /metrics từ bên ngoài
    ├── prometheus/
    │   ├── prometheus.yml           #   Scrape config (DNS service discovery)
    │   ├── alert_rules.yml          #   4 alert: error rate, p95, instance down, cost
    │   └── alert_rules_test.yml     #   Unit test cho alert
    ├── grafana/
    │   ├── provisioning/            #   Datasource + dashboard tự load
    │   └── dashboards/agent-overview.json
    ├── locust/locustfile.py         #   Load test mô phỏng người dùng
    └── utils/mock_llm.py
```

### Agent v1 vs v2

Cả hai bước đều có 2 phiên bản agent (chọn bằng biến `AGENT_VERSION`) để bạn có thứ để **so sánh**:

| | v1 | v2 |
|---|---|---|
| Pipeline | Hỏi thẳng LLM | Tìm tài liệu (retrieval) → đưa vào prompt → hỏi LLM |
| Chất lượng | Thấp (trả lời chung chung) | Cao hơn nhiều |
| Token / cost | Ít | Nhiều hơn (prompt dài hơn vì có context) |
| Latency | Nhanh hơn | Chậm hơn một chút (thêm bước retrieval) |

Đây là trade-off thật khi làm RAG: chất lượng tăng thì chi phí và latency cũng tăng. Benchmark giúp bạn biết đổi lại có đáng không.

---

## Bước 1 — Instrument & Benchmark local (`develop/`)

### 1.1 Chạy 2 phiên bản agent

```bash
cd develop
pip install -r requirements.txt

# Terminal 1 — agent v1
AGENT_VERSION=v1 PORT=8001 python app.py

# Terminal 2 — agent v2
AGENT_VERSION=v2 PORT=8002 python app.py
```

Thử gọi:

```bash
curl -s -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "Prometheus thu thập metrics như thế nào?"}' | python -m json.tool
```

Response có thêm `usage` (token, cost) và `request_id` — agent tự báo cáo chi phí của từng request.

### 1.2 Xem metrics

Mở http://localhost:8002/metrics. Đây chính là thứ Prometheus sẽ đọc:

```
http_requests_total{method="POST",route="/ask",status="200"} 1.0
http_request_duration_seconds_bucket{le="0.2",method="POST",route="/ask"} 1.0
llm_tokens_total{model="mock-llm-small",type="input"} 81.0
llm_cost_usd_total{model="mock-llm-small"} 3.975e-05
agent_info{model="mock-llm-small",version="v2"} 1.0
```

Mở `app.py`, tìm middleware `observe_requests`: vì sao label `route` dùng `route.path` (template) chứ không dùng URL thật?

### 1.3 Benchmark hiệu năng

```bash
python load_test.py --url http://localhost:8002 --requests 300 --concurrency 20
```

```
════════════════════════════════════════════════════════
  LOAD TEST REPORT — http://localhost:8002
════════════════════════════════════════════════════════
  Requests     : 300  (concurrency=20)
  Throughput   : 65.16 req/s
  Error rate   : 1.33%   {'200': 296, '503': 4}
  Latency (ms) : min=134.8  avg=224.6  max=1692.1
                 p50=168.7  p90=194.5  p95=202.0  p99=1685.0
────────────────────────────────────────────────────────
  SLO p95 ≤ 500ms        : ✅ PASS
  SLO error rate ≤ 2.0%  : ✅ PASS
════════════════════════════════════════════════════════
```

Nhìn **avg = 224ms** nhưng **p99 = 1685ms**: chính là tail latency.

Các tham số hữu ích: `--duration 30` (chạy theo thời gian), `--slo-p95-ms`, `--slo-error-rate`, `--out reports/load.json` (lưu report). Exit code = 1 khi vi phạm SLO, nên dùng được trong CI.

### 1.4 Benchmark chất lượng

```bash
# Eval v1, lưu làm baseline
python eval/run_eval.py --url http://localhost:8001 --out reports/eval_v1.json

# Eval v2, so sánh với v1
python eval/run_eval.py --url http://localhost:8002 --out reports/eval_v2.json \
       --baseline reports/eval_v1.json
```

```
  EVAL REPORT — agent v2
  Accuracy      : 91.3%  (21/23)
  Total cost    : $0.000759   (cost / câu đúng: $0.00003616)

  📊 So sánh với baseline (agent v1)
  Chỉ số                    Baseline    Hiện tại        Thay đổi
  Accuracy                      4.3%       91.3%    +87.0 điểm %
  Avg tokens / câu              56.7       109.0           +52.3
  Tổng cost (USD)           0.000398    0.000759       +0.000361

  ✨ Câu được cải thiện : 20
  ⚠️  Câu bị regression : 0
  Quality gate (accuracy ≥ 80%, không regression): ✅ PASS
```

Cách chấm: mỗi câu trong `golden_dataset.jsonl` có danh sách `expected_keywords`; câu đúng khi câu trả lời chứa ≥ 60% keyword. Trong thực tế bạn có thể dùng semantic similarity hoặc LLM-as-judge, nhưng quy trình giữ nguyên: **dataset cố định → chạy → so sánh với baseline → chặn nếu tệ đi**.

`cost / câu đúng` là chỉ số tốt để so sánh 2 phiên bản: v2 tốn gấp đôi token mỗi câu nhưng **rẻ hơn ~10 lần trên mỗi câu trả lời đúng**.

### Bài tập Bước 1

1. **Concurrency vs latency:** chạy load test với `--concurrency` 1, 10, 50, 100 cho v2. Ghi RPS và p95 vào bảng. RPS có tăng tuyến tính không? Vì sao?
2. **Tail latency:** khởi động lại v2 với `MOCK_SLOW_RATE=0` rồi `MOCK_SLOW_RATE=0.1`. So sánh avg, p95, p99. Chỉ số nào thay đổi nhiều nhất?
3. **Sửa lỗi bằng eval:** v2 sai 2 câu (`scaling-03`, `security-02`). Đọc report để biết thiếu keyword gì, thêm entry vào `KNOWLEDGE_BASE` trong `utils/mock_llm.py`, chạy lại eval với `--baseline reports/eval_v2.json` cho tới khi đạt 100% mà không có regression.
4. **Quality gate:** chạy eval cho v1 với `--baseline reports/eval_v2.json`. Exit code là gì? Viết 1 bước GitHub Actions dùng lệnh này để chặn merge.

---

## Bước 2 — Full Observability Stack (`production/`)

### Kiến trúc

```
                  ┌──────────────┐
  Locust :8089 ──▶│ Nginx :8080  │──┬──▶ agent-1 ──┐
  (load test)     │ (LB, chặn    │  └──▶ agent-2 ──┤  OTLP/HTTP
                  │  /metrics)   │                 │  (traces)
                  └──────────────┘        ▲        ▼
                                          │   ┌──────────────┐
                              scrape      │   │ Jaeger :16686│
                              /metrics    │   └──────────────┘
                                   ┌──────┴───────┐
                                   │ Prometheus   │ ◀── alert_rules.yml
                                   │ :9090        │
                                   └──────▲───────┘
                                          │ query
                                   ┌──────┴───────┐
                                   │ Grafana :3000│ ◀── dashboard provision sẵn
                                   └──────────────┘
```

### 2.1 Khởi động

```bash
cd production
docker compose up --build -d
docker compose ps        # chờ tất cả "running"
```

| Service | URL | Ghi chú |
|---|---|---|
| Agent (qua Nginx) | http://localhost:8080 | Header `X-API-Key: day14-lab-key` |
| Grafana | http://localhost:3000 | Dashboard "Day 14 — AI Agent Overview" (admin/admin) |
| Prometheus | http://localhost:9090 | *Status → Target health*, *Alerts* |
| Jaeger | http://localhost:16686 | Service `observable-agent` |
| Locust | http://localhost:8089 | Web UI để tạo tải |

Kiểm tra nhanh:

```bash
curl -s -i -X POST http://localhost:8080/ask \
  -H "X-API-Key: day14-lab-key" -H "Content-Type: application/json" \
  -d '{"question": "Trong distributed tracing, span là gì?"}'
```

Response header có `X-Trace-ID` và `X-Served-By`. Gọi vài lần sẽ thấy `X-Served-By` đổi giữa 2 instance.

```bash
curl -i http://localhost:8080/metrics   # → 404: /metrics không public ra ngoài
```

### 2.2 Tạo tải bằng Locust

**Web UI:** mở http://localhost:8089 → Number of users `30`, Ramp up `5` → **Start**. Vừa chạy vừa mở Grafana để xem dashboard thay đổi.

**Headless** (cho benchmark có số liệu lưu lại):

```bash
docker compose run --rm locust -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 30 -r 5 -t 2m --csv /mnt/results/v2_2replicas
# Kết quả: benchmark-results/v2_2replicas_stats.csv
```

Locust trả exit code 1 nếu có request lỗi, chuyện này bình thường vì mock LLM có ~1% lỗi.

### 2.3 Đọc dashboard Grafana

Dashboard có 4 hàng:

| Hàng | Panel | Đọc thế nào |
|---|---|---|
| 🚦 Golden Signals | Traffic, Error rate, p95, In-flight, Instances, Cost 1h | Màu đỏ = vi phạm ngưỡng |
| ⏱️ Latency & Traffic | p50/p95/p99/avg theo thời gian; req/s theo status | Khoảng cách giữa avg và p99 = tail latency |
| 🤖 LLM | LLM p95, tokens/s, cost USD/giờ, calls ok/error, retrieval hit ratio | Cost tăng khi đổi model hoặc prompt dài hơn |
| 🧩 Instances | req/s theo instance, bảng version đang chạy | LB có chia đều không? Bản nào đang phục vụ? |

Click **Edit** trên một panel để xem câu PromQL phía sau. Ví dụ p95:

```promql
histogram_quantile(0.95,
  sum by (le) (rate(http_request_duration_seconds_bucket{job="agent", route="/ask"}[1m])))
```

### 2.4 Tìm request chậm bằng Jaeger

1. Mở http://localhost:16686 → Service `observable-agent` → **Find Traces**
2. Sắp xếp theo **Longest First** → mở trace chậm nhất
3. Xem span nào chiếm nhiều thời gian:

```
POST /ask                          1712ms
└── agent.run                      1709ms
    ├── agent.retrieve_context       21ms
    └── chat mock-llm-small        1687ms   ← LLM chậm, không phải code của bạn
        gen_ai.request.model = mock-llm-small
        gen_ai.usage.input_tokens = 81
        gen_ai.usage.output_tokens = 46
```

Tìm trace lỗi: thêm Tags `error=true`. Span `chat ...` có event `exception` ghi lại lỗi upstream.

**Từ log sang trace:** mọi dòng log JSON đều có `trace_id`:

```bash
docker compose logs agent | grep llm_error
# {"event": "llm_error", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736", ...}
```

Dán `trace_id` vào ô tìm kiếm của Jaeger để xem toàn bộ request đó.

### 2.5 Prometheus & Alerts

Mở http://localhost:9090 và thử các câu query:

| Mục đích | PromQL |
|---|---|
| Req/s theo instance | `sum by (instance) (rate(http_requests_total{route="/ask"}[1m]))` |
| Error rate | `sum(rate(http_requests_total{route="/ask",status=~"5.."}[1m])) / sum(rate(http_requests_total{route="/ask"}[1m]))` |
| p99 LLM latency | `histogram_quantile(0.99, sum by (le) (rate(llm_request_duration_seconds_bucket[5m])))` |
| Token/giây | `sum by (type) (rate(llm_tokens_total[1m]))` |
| Cost ước tính/giờ | `sum(rate(llm_cost_usd_total[5m])) * 3600` |

Tab **Alerts** hiển thị 4 rule trong `alert_rules.yml`. Mỗi alert đi qua `inactive → pending → firing`. Trạng thái `pending` nghĩa là điều kiện đúng nhưng chưa kéo dài đủ thời gian `for`.

Alert cũng là code, nên phải được test:

```bash
docker run --rm -v "$PWD/prometheus:/p" --entrypoint promtool \
  prom/prometheus:v3.16.0 test rules /p/alert_rules_test.yml
```

### 2.6 Bài tập — Phá hệ thống và quan sát

Đổi cấu hình bằng file `.env` (`cp .env.example .env`), sau đó `docker compose up -d` để áp dụng. Giữ Locust chạy ~30 user trong suốt bài tập.

| # | Thay đổi | Quan sát | Alert mong đợi |
|---|---|---|---|
| A | `MOCK_ERROR_RATE=0.2` | Panel Error rate đỏ; trace lỗi trong Jaeger | `AgentHighErrorRate` sau ~1 phút |
| B | `MOCK_SLOW_RATE=0.3` | p95/p99 tăng vọt, avg tăng ít hơn | `AgentHighLatencyP95` sau ~2 phút |
| C | `LLM_MODEL=mock-llm-large` | Cost USD/giờ tăng ~16 lần, latency tăng | `LLMCostBurnRateHigh` |
| D | `docker pause production-agent-1` (xem tên bằng `docker compose ps`) | Instances up giảm; Locust thấy vài lỗi 504 nhưng panel Error rate của agent vẫn xanh | `AgentInstanceDown` |
| E | `AGENT_VERSION=v1` | Retrieval hit ratio mất, tokens/s giảm, bảng version đổi | Không có. Lỗi chất lượng không làm metric hạ tầng đỏ, chỉ eval mới bắt được |

Với mỗi thí nghiệm: chụp màn hình dashboard + alert, ghi lại **mất bao lâu từ lúc đổi đến lúc alert firing**. Nhớ `docker unpause` và trả `.env` về như cũ sau khi xong.

> 💡 Bài D có 2 điều đáng suy nghĩ:
> - **Vì sao `docker pause` mà không `docker stop`?** Prometheus tìm agent bằng DNS. Container bị stop sẽ biến mất khỏi DNS, Prometheus coi như target không còn tồn tại (không phải "down") nên không có alert. Container bị pause vẫn còn trong DNS nhưng không trả lời → `up == 0`.
> - **Vì sao lỗi 504 không hiện trên dashboard?** Lỗi xảy ra ở Nginx (chờ quá `proxy_read_timeout`), request chưa bao giờ được agent xử lý xong, nên metric của agent không đếm nó. Metric chỉ phản ánh những gì *chính service đó* nhìn thấy, vì vậy production cần thêm giám sát ở tầng load balancer hoặc synthetic check từ bên ngoài.

### 2.7 Benchmark scaling

```bash
# 1 instance
docker compose up -d --scale agent=1
docker compose run --rm locust -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_1

# 3 instance
docker compose up -d --scale agent=3
docker compose run --rm locust -f /mnt/locust/locustfile.py --host http://nginx \
  --headless -u 100 -r 10 -t 2m --csv /mnt/results/scale_3
```

Không cần restart gì cả: Prometheus (`dns_sd_configs`) và Nginx (`server agent:8000 resolve`) đều hỏi lại Docker DNS mỗi 10 giây nên tự thấy instance mới. Chờ ~15 giây rồi kiểm tra panel "Requests/s theo instance" trước khi chạy Locust.

So sánh RPS, p95, error rate giữa 2 lần chạy rồi giải thích. Nếu scale lên 3 mà p95 gần như không đổi, nút thắt nằm ở đâu? (Gợi ý: xem panel LLM latency.)

### 2.8 Dọn dẹp

```bash
docker compose down
```

---

## Nộp bài

Điền [BENCHMARK_REPORT_TEMPLATE.md](BENCHMARK_REPORT_TEMPLATE.md), lưu thành `BENCHMARK_REPORT.md` trong repo của bạn, kèm:

- `reports/` — JSON report của load test và eval (v1, v2)
- `benchmark-results/` — CSV của Locust (scale 1 vs 3)
- Screenshot: dashboard Grafana khi có tải, 1 trace chậm trong Jaeger, 1 alert đang firing

---

## Câu hỏi thảo luận

1. Agent v2 có error rate và latency tốt, dashboard toàn màu xanh, nhưng người dùng phàn nàn câu trả lời sai. Observability hạ tầng có phát hiện được không? Cần thêm gì?
2. Vì sao Prometheus dùng mô hình **pull** (scrape)? Agent chạy trên Cloud Run (scale về 0) thì pull còn phù hợp không?
3. Nếu thêm label `user_id` vào `http_requests_total` với 100.000 user thì chuyện gì xảy ra?
4. Trace mọi request có tốn kém không? Khi traffic lớn, *sampling* (vd chỉ giữ 10% trace) đánh đổi điều gì?
5. Alert error rate dùng `for: 1m`. Đặt `for: 0s` hoặc `for: 30m` thì có vấn đề gì?

---

## Đọc thêm

- Google SRE Book — [Monitoring Distributed Systems](https://sre.google/sre-book/monitoring-distributed-systems/) (4 Golden Signals)
- [Prometheus — Histograms and summaries](https://prometheus.io/docs/practices/histograms/)
- [Prometheus — Metric and label naming](https://prometheus.io/docs/practices/naming/)
- [OpenTelemetry — Semantic conventions for GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Locust documentation](https://docs.locust.io/)
