"""
Locust load test — mô phỏng NGƯỜI DÙNG THẬT gọi agent.

Khác load_test.py (develop): Locust có web UI, ramp-up số user dần dần,
vẽ biểu đồ realtime, xuất CSV — là công cụ benchmark chuẩn trong industry.

Chạy qua docker compose (đã cấu hình sẵn):
  Web UI   : http://localhost:8089  → nhập số user, spawn rate → Start
  Headless : docker compose run --rm locust -f /mnt/locust/locustfile.py \
               --headless -u 30 -r 5 -t 2m --csv /mnt/results/run1

Mỗi user: hỏi agent (phần lớn thời gian), thỉnh thoảng gọi /health,
nghỉ 0.5–2 giây giữa các lần (giống người thật đọc câu trả lời).
"""
import os
import random

from locust import HttpUser, between, task

API_KEY = os.getenv("AGENT_API_KEY", "")

QUESTIONS = [
    "Docker container là gì?",
    "Multi-stage build trong Dockerfile có lợi ích gì?",
    "Readiness probe fail thì chuyện gì xảy ra?",
    "Graceful shutdown xử lý SIGTERM như thế nào?",
    "Vì sao SLO latency nên dùng p95 thay vì trung bình?",
    "Prometheus thu thập metrics theo cơ chế push hay pull?",
    "Trong distributed tracing, span là gì?",
    "Chi phí gọi LLM được tính dựa trên những gì?",
    "Kubernetes HPA scale dựa trên những chỉ số nào?",
]


class AgentUser(HttpUser):
    wait_time = between(0.5, 2)

    def on_start(self):
        self.headers = {"X-API-Key": API_KEY} if API_KEY else {}

    @task(10)
    def ask(self):
        question = random.choice(QUESTIONS)
        # name="/ask" → gom mọi câu hỏi vào 1 dòng thống kê
        with self.client.post("/ask", json={"question": question}, headers=self.headers,
                              name="/ask", catch_response=True) as resp:
            if resp.status_code == 200 and not resp.json().get("answer"):
                resp.failure("Response 200 nhưng answer rỗng")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")
