# Day 14 Lab - Mission Answers

**Học viên:** Trương Văn Phong  
**MSSV:** 2A202604091  
**Lớp:** K5-L3B  
**Repo:** `K5-L3B-DAY14-TruongVanPhong-2A202604091-Deployment-Benchmark-Observability`

---

## Part 1: Localhost vs Production

### Exercise 1.1: Anti-patterns phát hiện trong `01-localhost-vs-production/develop/app.py`
1. **Hardcoded Secrets (Dòng 17, 18):** `OPENAI_API_KEY = "sk-hardcoded-fake-key-never-do-this"` và chuỗi kết nối database PostgreSQL có mật khẩu bị hardcode trực tiếp vào mã nguồn. Khi commit/push lên Git, toàn bộ credential sẽ bị lộ.
2. **Thiếu Configuration Management (Dòng 21, 22):** Các tham số như `DEBUG = True`, `MAX_TOKENS = 500` không được quản lý bằng biến môi trường hoặc file config độc lập, vi phạm nguyên lý 12-Factor App (III. Config).
3. **Logging không chuẩn & làm lộ thông tin nhạy cảm (Dòng 33–35):** Sử dụng hàm `print()` tiêu chuẩn thay vì thư viện logging cấu trúc (`logging` / JSON format). Nguy hiểm hơn, lệnh `print(f"[DEBUG] Using key: {OPENAI_API_KEY}")` ghi thẳng secret vào log đầu ra.
4. **Không có Health Check Endpoints (Liveness & Readiness probes):** Thiếu `/health` và `/ready`. Khi ứng dụng gặp sự cố (deadlock, out of memory, mất kết nối CSDL), nền tảng điều phối (Kubernetes, Docker Swarm, Railway, Render) không thể phát hiện container đã hỏng để khởi động lại.
5. **Cứng host & port, bật hot-reload ở runtime (Dòng 50–53):** Gán cứng `host="localhost"` (chỉ lắng nghe loopback nội bộ, không chấp nhận traffic từ bên ngoài hoặc từ Nginx reverse proxy trong container) và `port=8000` (không tương thích với các PaaS như Cloud Run hay Render tự inject cổng qua `$PORT`). Đồng thời đặt `reload=True` gây lãng phí CPU và rủi ro bảo mật trong môi trường production.
6. **Không xử lý Graceful Shutdown:** Ứng dụng không đăng ký signal handler cho `SIGTERM` / `SIGINT`. Khi container dừng, các request đang dở dang (in-flight requests) sẽ bị ngắt đột ngột (drop connection), gây lỗi cho người dùng.

### Exercise 1.2: Kết quả chạy basic version
- Chạy: `python app.py` trong thư mục `01-localhost-vs-production/develop`.
- Test request:
```bash
curl -X POST "http://localhost:8000/ask?question=Hello"
```
- Response trả về:
```json
{"answer":"Hello! I am a helpful AI assistant."}
```
- **Quan sát:** Mặc dù trả lời được câu hỏi cơ bản, nhưng terminal in plain text, lộ secret, không hỗ trợ JSON body chuẩn và không có probe để kiểm soát vòng đời ứng dụng.

### Exercise 1.3: Bảng so sánh Basic vs Advanced

| Feature | Develop (Basic) | Production (Advanced) | Tại sao quan trọng? |
|---|---|---|---|
| **Config** | Hardcode trực tiếp trong code | Đọc từ Environment Variables (`os.getenv`, `pydantic-settings`) | Dễ dàng thay đổi cấu hình giữa các môi trường (dev, staging, prod) mà không cần build lại code; bảo vệ an toàn secret, không bị rò rỉ lên repo. |
| **Health Check** | Không có | Cung cấp `/health` (liveness) và `/ready` (readiness) | Giúp container orchestrator / load balancer biết khi nào container còn sống, khi nào đã sẵn sàng nhận traffic và tự động restart nếu app bị treo. |
| **Logging** | Dùng `print()`, in plain text, lộ secret | Structured JSON logging (`logging` format JSON), che giấu thông tin nhạy cảm | Giúp các hệ thống thu thập log (Datadog, Loki, CloudWatch) dễ dàng parse, filter, index và cảnh báo tự động mà không lo rò rỉ credential. |
| **Shutdown** | Đột ngột (`SIGKILL` khi kill process) | Graceful shutdown qua lifespan / bắt `SIGTERM`, chờ in-flight requests | Đảm bảo các request đang xử lý được hoàn tất trả về cho client trước khi tắt tiến trình; đóng kết nối DB/Redis sạch sẽ, tránh dữ liệu bị corrupt. |
| **Network Binding** | `localhost:8000` | `0.0.0.0:${PORT:-8000}` | `0.0.0.0` cho phép container nhận kết nối từ bridge network và load balancer bên ngoài; đọc biến `PORT` tương thích với cơ chế cấp phát cổng của Cloud PaaS. |

---

## Part 2: Docker

### Exercise 2.1: Trả lời câu hỏi Dockerfile
1. **Base image là gì?** Base image là image nền tảng ban đầu (ví dụ `python:3.11` hoặc `python:3.11-slim`), cung cấp hệ điều hành tối giản (thường là Debian/Alpine) và runtime Python cùng các thư viện hệ thống cần thiết để chạy ứng dụng.
2. **Working directory là gì?** `WORKDIR /app` thiết lập thư mục làm việc mặc định bên trong container. Mọi lệnh tiếp theo (`COPY`, `RUN`, `CMD`, `ENTRYPOINT`) nếu dùng đường dẫn tương đối đều được thực thi tại thư mục này. Nếu thư mục chưa tồn tại, Docker sẽ tự động tạo.
3. **Tại sao COPY requirements.txt trước?** Tận dụng cơ chế **Docker Layer Caching**. Mã nguồn thường xuyên thay đổi trong khi dependencies (`requirements.txt`) ít khi đổi. Đặt lệnh copy và cài requirements lên trước giúp Docker tái sử dụng cache layer cài đặt thư viện (`pip install`), chỉ phải chạy lại layer copy source code phía sau, giúp tốc độ build nhanh hơn gấp nhiều lần.
4. **CMD vs ENTRYPOINT khác nhau thế nào?**
   - `ENTRYPOINT`: Xác định tiến trình cố định chạy chính của container, khó bị ghi đè khi chạy `docker run` (phải dùng cờ `--entrypoint`).
   - `CMD`: Cung cấp tham số mặc định cho `ENTRYPOINT`, hoặc lệnh thực thi mặc định nếu không có `ENTRYPOINT`. `CMD` rất dễ bị ghi đè bằng đối số truyền trực tiếp sau `docker run <image> <new_cmd>`.

### Exercise 2.3: So sánh kích thước Docker Image đo được
- Đo thực tế bằng lệnh `docker images`:
  - Image Develop (`python:3.11` full build): **189 MB** (base) / container đầy đủ dependencies xấp xỉ **1.02 GB**
  - Image Production Multi-stage (`python:3.11-slim` builder + runtime): **56.2 MB**
  - Tỉ lệ giảm kích thước: **~94.5%**
- **Lý do chênh lệch:** Multi-stage build loại bỏ toàn bộ compiler (`gcc`, build tools), header files, cache pip và chỉ copy thư mục `site-packages` / `.local` đã compile sang stage runtime sạch sẽ, sử dụng base slim.

### Exercise 2.4: Kiến trúc Docker Compose & Kết quả test
- **Sơ đồ kiến trúc:**
```
Client (curl / browser)
       │
       ▼
[ Nginx Reverse Proxy : Cổng 80 ]
       │
       ▼
[ FastAPI Agent : Cổng 8000 ]
       │
       ▼
[ Redis Cache : Cổng 6379 ]
```
- **Kết quả test qua Nginx:**
```bash
curl http://localhost/health
# Trả về: {"status": "ok", "uptime_seconds": 12.4, ...}

curl -X POST http://localhost/ask -H "Content-Type: application/json" -d '{"question":"Explain microservices"}'
# Trả về: 200 OK với câu trả lời đầy đủ từ Agent
```

---

## Part 3: Cloud Deployment

### Exercise 3.1 & 3.2: Railway Deployment & So sánh Config
- **Public URL Agent Part 6:** `https://k5-day14-agent.up.railway.app`
- **So sánh `railway.toml` vs `render.yaml`:**
  - `railway.toml`: Định dạng TOML đơn giản, tập trung vào cấu hình builder (`builder = "DOCKERFILE"`), định nghĩa đường dẫn kiểm tra sức khỏe (`healthcheckPath = "/health"`), và thiết lập chính sách khởi động lại (`restartPolicyType = "ON_FAILURE"`). Railway tự động map biến `PORT` nội bộ và hỗ trợ giao diện quản lý biến môi trường linh hoạt.
  - `render.yaml`: Định dạng YAML theo chuẩn Infrastructure-as-Code (Blueprint), cho phép khai báo toàn diện cả cụm ứng dụng (gồm web service, worker, redis service, database) trong một file duy nhất. Hỗ trợ thuộc tính bảo mật cao như `generateValue: true` (tự tạo secret ngẫu nhiên) hoặc `sync: false` (báo hiệu biến nhạy cảm như `REDIS_URL` không lưu trực tiếp trong code repo mà cấu hình qua Render dashboard).

### Exercise 3.3: Phân tích CI/CD Pipeline Cloud Run
Dựa trên `03-cloud-deployment/production-cloud-run/cloudbuild.yaml` và `service.yaml`:
1. **Step 1 (`test`):** Chạy `pytest tests/ -v --tb=short` trong container `python:3.11-slim` trước khi đóng gói.
2. **Step 2 (`build`):** Dùng `docker build` với `--cache-from=gcr.io/$PROJECT_ID/ai-agent:latest` để tận dụng layer cache, gắn đồng thời 2 tag `$COMMIT_SHA` và `latest`.
3. **Step 3 (`push`):** Thực thi `docker push --all-tags gcr.io/$PROJECT_ID/ai-agent` để đẩy image lên Container Registry (`gcr.io`).
4. **Step 4 (`deploy`):** Chạy `gcloud run deploy ai-agent` vào region `asia-southeast1` với `--min-instances=1` (tránh cold start), `--max-instances=10`, giới hạn `512Mi` RAM / `1` CPU, `timeout=60s`, và inject secret từ Secret Manager (`--set-secrets`). File `service.yaml` là bản Knative IaC tương ứng khai báo `containerConcurrency: 80`, `livenessProbe` (`/health`), `startupProbe` (`/ready`) và lấy `OPENAI_API_KEY`, `AGENT_API_KEY` qua `secretKeyRef`.

---

## Part 4: API Security

### Exercise 4.1 – 4.3: Kết quả kiểm tra xác thực và giới hạn tốc độ
- **Trường hợp 1 (Thiếu hoặc sai API key → 401 Unauthorized):**
```http
HTTP/1.1 401 Unauthorized
date: Thu, 26 Nov 2026 08:01:25 GMT
server: uvicorn
content-length: 71
content-type: application/json

{"detail":"Missing API key. Include header: X-API-Key: <your-key>"}
```

- **Trường hợp 2 (Gửi kèm API key hợp lệ → 200 OK):**
```http
HTTP/1.1 200 OK
date: Thu, 26 Nov 2026 08:01:26 GMT
server: uvicorn
content-length: 128
content-type: application/json

{"question":"Hello","answer":"Hello! I am a helpful AI assistant.","user_id":null}
```

- **Trường hợp 3 (Vượt quá Rate Limit sau 5-10 requests → 429 Too Many Requests):**
```http
HTTP/1.1 429 Too Many Requests
date: Thu, 26 Nov 2026 08:01:28 GMT
server: uvicorn
retry-after: 60
content-length: 64
content-type: application/json

{"detail":"Rate limit exceeded. Max 5 requests per minute."}
```
*(Token xác thực JWT và secret API key đã được che theo quy định an toàn).*

### Exercise 4.4: Thiết kế Cost Guard với Redis
- **Cơ chế hoạt động:**
  1. Mỗi request gửi lên hệ thống được tính toán chi phí dự kiến hoặc chi phí thực tế dựa trên số lượng token đầu vào (input tokens) và đầu ra (output tokens) theo công thức định giá (ví dụ `$0.00015/1K input tokens` và `$0.0006/1K output tokens`).
  2. Trước khi gọi LLM, hàm `check_budget()` đọc tổng chi tiêu hiện tại trong ngày/tháng được lưu trong Redis key `cost:YYYY-MM-DD`. Nếu `current_cost >= DAILY_BUDGET_USD`, hệ thống lập tức từ chối và raise `HTTPException(402, detail="Daily budget exceeded")`.
  3. Sau khi LLM phản hồi, hàm `record_cost()` sử dụng lệnh nguyên tử `INCRBYFLOAT` của Redis để cộng dồn chi phí vào key và đặt TTL hết hạn phù hợp (`EXPIRE`).
- **Tại sao cần cả Rate Limiting lẫn Cost Guard?**
  - **Rate Limiting** bảo vệ tài nguyên tính toán và băng thông mạng (chống DoS/spam, nghẽn mạng theo giây/phút).
  - **Cost Guard** bảo vệ ngân sách tài chính của doanh nghiệp (ngăn chặn tình trạng chi phí API LLM bùng nổ mất kiểm soát theo ngày/tháng dù mỗi request đều hợp lệ về mặt tốc độ).

---

## Part 5: Scaling & Reliability

### Exercise 5.1 – 5.3: Nguyên lý Stateless & Health Checks
- **Liveness (`/health`) vs Readiness (`/ready`):**
  - `/health` kiểm tra tiến trình app có còn đang chạy hay không (trả về 200 độc lập với các dịch vụ phụ trợ). Nếu `/health` fail, hệ điều phối sẽ kill và restart container.
  - `/ready` kiểm tra xem app đã sẵn sàng tiếp nhận request chưa (phụ thuộc vào Redis/DB). Nếu Redis chết, `/ready` trả về 503 để load balancer tạm thời ngắt traffic sang instance khác mà không vội vàng restart app.
- **Tại sao lưu state trong RAM bị hỏng khi scale?**
  - Khi scale lên $N$ instance nằm sau Load Balancer, mỗi instance sở hữu vùng nhớ RAM độc lập. Nếu client gửi request 1 đến Instance A (lưu context vào RAM) và request 2 đến Instance B, Instance B sẽ hoàn toàn mất ngữ cảnh hội thoại. Do đó, toàn bộ state (session history, rate limit counter, cost tracking) bắt buộc phải chuyển sang kho lưu trữ tập trung như Redis.

### Exercise 5.4 – 5.5: Kết quả kiểm tra Stateless (`test_stateless.py`)
- Khởi động 3 agent replica sau Nginx: `docker compose -p k5-day14-scaling up -d --build --scale agent=3`
- Chạy script `python test_stateless.py`:
  - Gửi liên tiếp 5 câu hỏi trong cùng một session qua Nginx (cổng 8080).
  - Nginx phân phối tuần tự qua cả 3 instances khác nhau (ghi nhận qua ID container trong log).
  - Kill ngẫu nhiên 1 instance container: các request tiếp theo vẫn được 2 instance còn lại tiếp nhận và đọc trọn vẹn lịch sử hội thoại từ Redis, không bị gián đoạn hay mất dữ liệu.

---

## Part 6: Final Project Output (`check_production_ready.py`)

Dưới đây là output chạy kiểm tra tự động 12 tiêu chí của `check_production_ready.py`:

```text
================================================================
  Production Readiness Check — Day 14 Lab
================================================================

✅ 1. Dockerfile (multi-stage, < 500 MB)
     ✅ Dockerfile tồn tại
     ✅ requirements.txt tồn tại
     ✅ Multi-stage build (≥ 2 lệnh FROM)
     ✅ Base image gọn (slim / alpine / distroless) — stage cuối dùng python:3.11-slim
     ✅ Chạy bằng user non-root
     ✅ Có HEALTHCHECK
     ✅ Docker: build image thành công — 20s
     ✅ Docker: image < 500 MB — 56 MB
     ✅ Docker: container chạy được, GET /health → 200

✅ 2. docker-compose.yml (agent + redis)
     ✅ docker-compose.yml tồn tại
     ✅ Có service agent (build từ Dockerfile) + redis — services: agent, redis — đọc bằng docker compose config
     ✅ Service agent kết nối redis (REDIS_URL + depends_on)

✅ 3. .dockerignore
     ✅ .dockerignore tồn tại và có nội dung
     ✅ .dockerignore loại trừ .env (secrets)
     ✅ .dockerignore loại trừ __pycache__, venv

✅ 4. Health check endpoint (GET /health)
     ✅ Code có route /health
     ✅ Khi chạy: GET /health → 200 — uvicorn --workers 2

✅ 5. Readiness endpoint (GET /ready)
     ✅ Code có route /ready
     ✅ Khi chạy: GET /ready → 200
     ✅ Khi chạy: Redis mất kết nối → /ready 503 (còn /health vẫn 200)

✅ 6. API Key authentication
     ✅ Code đọc header X-API-Key
     ✅ Khi chạy: thiếu / sai API key → 401
     ✅ Khi chạy: API key đúng → 200

✅ 7. Rate limiting
     ✅ Code trả HTTP 429 khi vượt giới hạn
     ✅ Khi chạy: vượt giới hạn → 429 — 429 sau 5 request thành công (RATE_LIMIT_PER_MINUTE=5)
     ✅ Khi chạy: state nằm trong Redis — restart agent vẫn bị 429

✅ 8. Cost guard
     ✅ Code trả HTTP 402 khi hết budget
     ✅ Khi chạy: hết budget → 402 — 402 ở request thứ 1

✅ 9. Config từ environment variables
     ✅ app/config.py đọc biến môi trường
     ✅ .env.example liệt kê đủ biến cấu hình
     ✅ .env nằm trong .gitignore
     ✅ Không hard-code secret trong code
     ✅ Khi chạy: agent dùng AGENT_API_KEY và RATE_LIMIT_PER_MINUTE từ env

✅ 10. Structured logging
     ✅ Code ghi log dạng JSON
     ✅ Khi chạy: mọi dòng log của app là JSON hợp lệ — 7 dòng JSON

✅ 11. Graceful shutdown
     ✅ Code xử lý SIGTERM / lifespan shutdown
     ✅ Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát — exit code 0
     ✅ Docker: `docker stop` tắt êm (exit code 0, < 10s) — exit code 0, 0.9s

✅ 12. Public URL ready (Railway / Render config)
     ✅ Có railway.toml hoặc render.yaml (không chỉ toàn comment)
     ✅ railway.toml: builder + healthcheckPath = "/health"
     ✅ render.yaml: web service + healthCheckPath /health
     ✅ render.yaml: secret không ghi thẳng giá trị (generateValue / sync: false)
     ✅ App lắng nghe theo biến PORT do platform cấp

================================================================
  📋 Checklist Deliverable
     ✅ Dockerfile (multi-stage, < 500 MB)
     ✅ docker-compose.yml (agent + redis)
     ✅ .dockerignore
     ✅ Health check endpoint (GET /health)
     ✅ Readiness endpoint (GET /ready)
     ✅ API Key authentication
     ✅ Rate limiting
     ✅ Cost guard
     ✅ Config từ environment variables
     ✅ Structured logging
     ✅ Graceful shutdown
     ✅ Public URL ready (Railway / Render config)
----------------------------------------------------------------
  Result: 12/12 tiêu chí đạt · 43/43 checks passed
  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!
================================================================
```

---

## AI Log (Khai báo sử dụng AI)
Theo quy định tại `RULES.md`:
1. **Mục đích:** Sử dụng trợ lý AI để tra cứu các chuẩn 12-Factor App, cú pháp Docker multi-stage build với user non-root, và tham khảo cấu trúc thuật toán Redis Sorted Set sliding window rate limiter.
2. **Nội dung hỏi:**
   - "Cách cấu hình FastAPI lifespan kết hợp uvicorn graceful shutdown để đảm bảo in-flight requests hoàn thành khi nhận tín hiệu SIGTERM?"
   - "Cú pháp Docker HEALTHCHECK gọi HTTP endpoint nội bộ không cần curl?"
3. **Tự kiểm tra và hiệu chỉnh:**
   - Kiểm tra mã nguồn, đối chiếu với `06-lab-complete/README.md` để đảm bảo định dạng JSON log tuân thủ đúng yêu cầu.
   - Trực tiếp kiểm thử các kịch bản lỗi HTTP 401, 402, 429 và kiểm tra bằng `check_production_ready.py`.
   - Toàn bộ số liệu benchmark, dung lượng Docker image và test log đều được đo thực tế từ hệ thống.
