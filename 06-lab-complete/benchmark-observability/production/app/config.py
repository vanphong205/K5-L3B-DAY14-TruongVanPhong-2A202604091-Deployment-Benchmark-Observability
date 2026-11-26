"""Config — 12-Factor: mọi thứ đọc từ biến môi trường."""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "observable-agent")
    app_version: str = os.getenv("APP_VERSION", "1.0.0")
    environment: str = os.getenv("ENVIRONMENT", "development")
    port: int = int(os.getenv("PORT", "8000"))

    # Agent
    agent_version: str = os.getenv("AGENT_VERSION", "v2")          # v1 | v2
    llm_model: str = os.getenv("LLM_MODEL", "mock-llm-small")       # mock-llm-small | mock-llm-large
    agent_api_key: str = os.getenv("AGENT_API_KEY", "")              # rỗng = tắt auth (chỉ dùng local)

    # Tracing — OTel SDK tự đọc OTEL_EXPORTER_OTLP_ENDPOINT, OTEL_SERVICE_NAME...
    tracing_enabled: bool = os.getenv("TRACING_ENABLED", "true").lower() == "true"
    otlp_endpoint: str = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    instance_id: str = field(default_factory=lambda: os.getenv("HOSTNAME", "local"))


settings = Settings()
