"""
Production Readiness Checker — Day 14 Lab

Kiểm tra project theo đúng 12 tiêu chí trong "Checklist Deliverable" của README:

   1. Dockerfile (multi-stage, < 500 MB)       7. Rate limiting
   2. docker-compose.yml (agent + redis)       8. Cost guard
   3. .dockerignore                            9. Config từ environment variables
   4. Health check endpoint (GET /health)     10. Structured logging
   5. Readiness endpoint (GET /ready)         11. Graceful shutdown
   6. API Key authentication                  12. Public URL ready (Railway / Render config)

Mỗi tiêu chí được kiểm tra bằng 3 lớp:
  • Tĩnh    : đọc file (Dockerfile, compose, config, code...)
  • Runtime : tự khởi động agent bằng uvicorn (giống CMD trong Dockerfile) rồi gọi API thật
  • Docker  : build image, đo kích thước, chạy container, `docker stop` xem có tắt êm không

Chạy từ thư mục chứa app/ (sau khi `pip install -r requirements.txt`):
  python check_production_ready.py                     # đầy đủ (lần đầu build image mất vài phút)
  python check_production_ready.py --skip-docker       # bỏ phần build/chạy Docker image
  python check_production_ready.py --static-only       # chỉ kiểm tra tĩnh
  python check_production_ready.py --public-url https://your-app.railway.app

Bài test stateless cần Redis: dùng --redis-url / REDIS_URL nếu có, không thì tự bật
redis-server hoặc container redis:7-alpine (cần Docker).

Kết quả: ✅ đạt · ❌ chưa đạt · ⚠️ chưa kiểm tra được (thiếu Docker, đang chạy chế độ nhanh...).
Chỉ PASS (exit code 0) khi cả 12 tiêu chí đều ✅ — mục ⚠️ cũng tính là chưa đạt,
vì vậy khi chấm bài cần chạy đầy đủ trên máy có Docker.

Yêu cầu chi tiết (endpoint, header, biến môi trường...) xem README.md, mục "Hợp đồng".
"""
import argparse
import ast
import atexit
import io
import importlib.util
import json
import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import urllib.error
import urllib.parse
import urllib.request

CRITERIA = [
    "Dockerfile (multi-stage, < 500 MB)",
    "docker-compose.yml (agent + redis)",
    ".dockerignore",
    "Health check endpoint (GET /health)",
    "Readiness endpoint (GET /ready)",
    "API Key authentication",
    "Rate limiting",
    "Cost guard",
    "Config từ environment variables",
    "Structured logging",
    "Graceful shutdown",
    "Public URL ready (Railway / Render config)",
]

MAX_IMAGE_MB = 500
RATE_LIMIT_FOR_TEST = 5      # ghi đè RATE_LIMIT_PER_MINUTE khi chạy thử
MAX_BURST = 40               # đủ cả khi giới hạn bị hard-code (vd 10–20 req/phút)
STARTUP_TIMEOUT = 25         # giây chờ agent trả /health 200
SHUTDOWN_TIMEOUT = 20        # giây chờ agent thoát sau SIGTERM
DOCKER_BUILD_TIMEOUT = 900
DOCKER_STOP_GRACE = 10       # docker stop mặc định chờ 10s rồi SIGKILL

RESULTS = []                 # {"criterion", "name", "passed", "detail"}

RT_HEALTH = (4, "Khi chạy: GET /health → 200")
RT_READY = (5, "Khi chạy: GET /ready → 200")
RT_READY_DOWN = (5, "Khi chạy: Redis mất kết nối → /ready 503 (còn /health vẫn 200)")
RT_AUTH_BAD = (6, "Khi chạy: thiếu / sai API key → 401")
RT_AUTH_OK = (6, "Khi chạy: API key đúng → 200")
RT_RATE = (7, "Khi chạy: vượt giới hạn → 429")
RT_STATELESS = (7, "Khi chạy: state nằm trong Redis — restart agent vẫn bị 429")
RT_COST = (8, "Khi chạy: hết budget → 402")
RT_ENV = (9, "Khi chạy: agent dùng AGENT_API_KEY và RATE_LIMIT_PER_MINUTE từ env")
RT_LOGS = (10, "Khi chạy: mọi dòng log của app là JSON hợp lệ")
RT_GRACEFUL = (11, "Khi chạy: SIGTERM lúc đang có request → request vẫn 200, process tự thoát")
RUNTIME_CHECKS = [RT_HEALTH, RT_READY, RT_READY_DOWN, RT_AUTH_BAD, RT_AUTH_OK, RT_RATE,
                  RT_STATELESS, RT_COST, RT_ENV, RT_LOGS, RT_GRACEFUL]
DK_BUILD = (1, "Docker: build image thành công")
DK_SIZE = (1, f"Docker: image < {MAX_IMAGE_MB} MB")
DK_RUN = (1, "Docker: container chạy được, GET /health → 200")
DK_STOP = (11, f"Docker: `docker stop` tắt êm (exit code 0, < {DOCKER_STOP_GRACE}s)")
DOCKER_CHECKS = [DK_BUILD, DK_SIZE, DK_RUN, DK_STOP]
_cleanup_actions = []


# ─────────────────────────────────────────────────────────
# Tiện ích chung
# ─────────────────────────────────────────────────────────
def record(criterion: int, name: str, passed, detail: str = "") -> bool:
    """passed: True = đạt, False = chưa đạt, None = bỏ qua."""
    RESULTS.append({"criterion": criterion, "name": name, "passed": passed, "detail": detail})
    return bool(passed)


def record_missing(checks, passed, detail):
    """Ghi các mục chưa được ghi (vd agent không khởi động được) để tiêu chí không bị ✅ oan."""
    done = {(r["criterion"], r["name"]) for r in RESULTS}
    for criterion, name in checks:
        if (criterion, name) not in done:
            record(criterion, name, passed, detail)


def progress(msg: str):
    print(f"  ⏳ {msg}", flush=True)


def _cleanup():
    while _cleanup_actions:
        try:
            _cleanup_actions.pop()()
        except Exception:
            pass


atexit.register(_cleanup)


def _indent(text: str, prefix: str = "        ") -> str:
    return "\n".join(prefix + line for line in text.splitlines())


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _strip_python(src: str) -> str:
    """Thay comment và docstring bằng khoảng trắng — TODO trong comment không được tính là đã làm."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    doc_starts = set()
    try:
        for node in ast.walk(ast.parse(src)):
            body = getattr(node, "body", None)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) \
                    and body and isinstance(body[0], ast.Expr) \
                    and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc_starts.add((body[0].lineno, body[0].col_offset))
    except SyntaxError:
        pass
    lines = src.splitlines(keepends=True)
    for tok in tokens:
        if tok.type == tokenize.COMMENT or (tok.type == tokenize.STRING and tok.start in doc_starts):
            (r1, c1), (r2, c2) = tok.start, tok.end
            for r in range(r1, r2 + 1):
                line = lines[r - 1]
                a = c1 if r == r1 else 0
                b = c2 if r == r2 else len(line.rstrip("\n"))
                lines[r - 1] = line[:a] + " " * (b - a) + line[b:]
    return "".join(lines)


def _strip_hash_comments(text: str) -> str:
    """Bỏ dòng comment (#) trong .dockerignore / TOML / YAML."""
    return "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))


def _app_sources(base: str) -> dict:
    """Toàn bộ file .py trong app/ (code có thể tách nhiều module)."""
    sources = {}
    app_dir = os.path.join(base, "app")
    for root, _, files in os.walk(app_dir):
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                sources[os.path.relpath(path, base)] = _read(path)
    return sources


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(method, url, headers=None, body=None, timeout=10):
    """Gọi HTTP bằng thư viện chuẩn. Trả về (status | None, headers, body)."""
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, \
                resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, {k.lower(): v for k, v in e.headers.items()}, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        return None, {}, str(e)


def _run(cmd, timeout=60, cwd=None):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 124, "", str(e))


def _last_lines(text: str, n=8) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


# ─────────────────────────────────────────────────────────
# Phân tích file cấu hình (không cần thư viện ngoài)
# ─────────────────────────────────────────────────────────
def _dockerfile_instructions(text: str) -> list:
    """Danh sách (INSTRUCTION, args) — đã nối dòng có dấu '\\' và bỏ comment."""
    instructions, buf = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        parts = buf.split(None, 1)
        if parts:
            instructions.append((parts[0].upper(), parts[1] if len(parts) > 1 else ""))
        buf = ""
    return instructions


def _env_keys(text: str) -> set:
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            keys.add(line.split("=", 1)[0].replace("export ", "").strip())
    return keys


def _compose_services(base: str, compose_path: str):
    """
    Trả về (services: dict, nguồn, lỗi).
    Ưu tiên `docker compose config` (chuẩn nhất), sau đó PyYAML, cuối cùng parse đơn giản.
    """
    if shutil.which("docker"):
        r = _run(["docker", "compose", "-f", compose_path, "config", "--format", "json"],
                 timeout=30, cwd=base)
        if r.returncode == 0:
            try:
                return json.loads(r.stdout).get("services", {}), "docker compose config", ""
            except ValueError:
                pass
        elif "unknown flag" not in r.stderr and "unknown command" not in r.stderr:
            return None, "docker compose config", _last_lines(r.stderr, 4)
    text = _read(compose_path)
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text) or {}
        return data.get("services", {}) or {}, "PyYAML", ""
    except ImportError:
        pass
    except Exception as e:
        return None, "PyYAML", f"YAML không hợp lệ: {e}"
    # Fallback: lấy tên service (thụt 2 space dưới services:) và giữ nguyên text từng block
    services, current, in_services = {}, None, False
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services and re.match(r"^\S", line):
            in_services = False
        if not in_services:
            continue
        m = re.match(r"^  ([A-Za-z0-9_.-]+):\s*$", line)
        if m:
            current = m.group(1)
            services[current] = {"_raw": ""}
        elif current:
            services[current]["_raw"] += line + "\n"
    for svc in services.values():
        raw = svc["_raw"]
        if re.search(r"^\s{4}build:", raw, re.M):
            svc["build"] = True
        m = re.search(r"^\s{4}image:\s*['\"]?([^\s'\"]+)", raw, re.M)
        if m:
            svc["image"] = m.group(1)
        m = re.search(r"REDIS_URL[=:]\s*['\"]?([^\s'\"]+)", raw)
        if m:
            svc["environment"] = {"REDIS_URL": m.group(1)}
        dep = re.search(r"^\s{4}depends_on:\s*\n((?:\s{6,}.*\n)+)", raw, re.M)
        if dep:
            svc["depends_on"] = re.findall(r"^\s{6}-?\s*([A-Za-z0-9_.-]+)", dep.group(1), re.M)
    return services, "parse đơn giản", ""


# ─────────────────────────────────────────────────────────
# 1. Kiểm tra tĩnh
# ─────────────────────────────────────────────────────────
def static_checks(base: str):
    progress("Kiểm tra tĩnh: file, Dockerfile, compose, config, code...")
    exists = lambda f: os.path.exists(os.path.join(base, f))
    raw_sources = _app_sources(base)
    sources = {path: _strip_python(code) for path, code in raw_sources.items()}
    all_code = "\n".join(sources.values())
    code_lower = all_code.lower()

    # ── 1. Dockerfile ──
    dockerfile = _read(os.path.join(base, "Dockerfile"))
    record(1, "Dockerfile tồn tại", bool(dockerfile))
    record(1, "requirements.txt tồn tại", exists("requirements.txt"))
    ins = _dockerfile_instructions(dockerfile)
    froms = [a for i, a in ins if i == "FROM"]
    record(1, "Multi-stage build (≥ 2 lệnh FROM)", len(froms) >= 2,
           f"{len(froms)} lệnh FROM" if len(froms) < 2 else "")
    final_image = froms[-1].split()[0] if froms else ""
    record(1, "Base image gọn (slim / alpine / distroless)",
           any(t in final_image for t in ("slim", "alpine", "distroless")),
           f"stage cuối dùng {final_image or '?'}")
    users = [a.strip() for i, a in ins if i == "USER"]
    record(1, "Chạy bằng user non-root", bool(users) and users[-1].split(":")[0] not in ("root", "0"),
           "thiếu lệnh USER" if not users else "")
    healthcheck = [a for i, a in ins if i == "HEALTHCHECK"]
    record(1, "Có HEALTHCHECK", bool(healthcheck) and healthcheck[-1].strip().upper() != "NONE")

    # ── 2. docker-compose.yml ──
    compose_name = next((f for f in ("docker-compose.yml", "docker-compose.yaml",
                                     "compose.yml", "compose.yaml") if exists(f)), None)
    record(2, "docker-compose.yml tồn tại", compose_name is not None)
    if compose_name:
        services, source, error = _compose_services(base, os.path.join(base, compose_name))
        if services is None:
            record(2, "Compose hợp lệ", False, f"{source}: {error}")
        else:
            agent_svc = {n: s for n, s in services.items() if s.get("build")}
            redis_svc = {n: s for n, s in services.items()
                         if str(s.get("image", "")).split("/")[-1].startswith("redis")}
            ok = bool(agent_svc) and bool(redis_svc)
            record(2, "Có service agent (build từ Dockerfile) + redis", ok,
                   f"services: {', '.join(services) or '(trống)'} — đọc bằng {source}")
            if ok:
                redis_name = next(iter(redis_svc))
                name, svc = next(iter(agent_svc.items()))
                env = svc.get("environment") or {}
                if isinstance(env, list):
                    env = dict(e.split("=", 1) for e in env if "=" in e)
                redis_url = str(env.get("REDIS_URL", ""))
                deps = svc.get("depends_on") or []
                deps = list(deps) if not isinstance(deps, dict) else list(deps.keys())
                ok_url = f"//{redis_name}" in redis_url or f"@{redis_name}" in redis_url
                record(2, f"Service {name} kết nối {redis_name} (REDIS_URL + depends_on)",
                       ok_url and redis_name in deps,
                       "" if ok_url and redis_name in deps else
                       f"REDIS_URL={redis_url or '(không có)'}, depends_on={deps}")

    # ── 3. .dockerignore ──
    dockerignore = _read(os.path.join(base, ".dockerignore"))
    patterns = [l.strip() for l in _strip_hash_comments(dockerignore).splitlines()
                if l.strip() and not l.strip().startswith("!")]
    record(3, ".dockerignore tồn tại và có nội dung", bool(patterns),
           "" if patterns else ("file trống / chỉ có comment" if dockerignore else "không có file"))
    record(3, ".dockerignore loại trừ .env (secrets)", any(".env" in p for p in patterns))
    missing = [w for w in ("__pycache__", "venv") if not any(w in p for p in patterns)]
    record(3, ".dockerignore loại trừ __pycache__, venv", not missing,
           f"thiếu: {', '.join(missing)}" if missing else "")

    # ── 4–8, 10, 11: code có đủ thành phần ──
    record(4, "Code có route /health", '"/health"' in all_code or "'/health'" in all_code)
    record(5, "Code có route /ready", '"/ready"' in all_code or "'/ready'" in all_code)
    record(6, "Code đọc header X-API-Key", "x-api-key" in code_lower or "x_api_key" in code_lower)
    record(7, "Code trả HTTP 429 khi vượt giới hạn", "429" in all_code)
    record(8, "Code trả HTTP 402 khi hết budget", "402" in all_code)
    record(10, "Code ghi log dạng JSON",
           any(t in all_code for t in ("json.dumps", "JsonFormatter", "jsonlogger", "structlog")))
    record(11, "Code xử lý SIGTERM / lifespan shutdown",
           any(t in all_code for t in ("SIGTERM", "lifespan", "on_event(\"shutdown\")")))

    # ── 9. Config từ environment variables ──
    config_code = sources.get(os.path.join("app", "config.py"), "")
    record(9, "app/config.py đọc biến môi trường",
           any(t in config_code for t in ("os.getenv", "os.environ", "BaseSettings")),
           "" if config_code else "không có app/config.py")
    example_keys = _env_keys(_read(os.path.join(base, ".env.example")))
    need = ["AGENT_API_KEY", "REDIS_URL", "RATE_LIMIT_PER_MINUTE"]
    missing = [k for k in need if k not in example_keys]
    if not ({"DAILY_BUDGET_USD", "MONTHLY_BUDGET_USD"} & example_keys):
        missing.append("DAILY_BUDGET_USD / MONTHLY_BUDGET_USD")
    record(9, ".env.example liệt kê đủ biến cấu hình",
           exists(".env.example") and not missing,
           "không có .env.example" if not exists(".env.example") else
           (f"thiếu: {', '.join(missing)}" if missing else ""))
    env_ignored = any(".env" in _read(os.path.join(base, p, ".gitignore")) for p in (".", ".."))
    record(9, ".env nằm trong .gitignore", env_ignored)
    secret_patterns = [r"sk-[A-Za-z0-9_-]{16,}", r"AKIA[0-9A-Z]{16}", r"password123",
                       r"ghp_[A-Za-z0-9]{30,}"]
    leaks = [f"{path}" for path, code in raw_sources.items()
             if any(re.search(p, code) for p in secret_patterns)]
    record(9, "Không hard-code secret trong code", not leaks, ", ".join(leaks))

    # ── 12. Public URL ready ──
    railway = _strip_hash_comments(_read(os.path.join(base, "railway.toml"))).strip()
    render = _strip_hash_comments(_read(os.path.join(base, "render.yaml"))).strip()
    record(12, "Có railway.toml hoặc render.yaml (không chỉ toàn comment)", bool(railway or render))
    if railway:
        ok = re.search(r'healthcheckPath\s*=\s*"/health"', railway) is not None
        builder_ok = re.search(r'builder\s*=\s*"(DOCKERFILE|NIXPACKS|RAILPACK)"', railway, re.I) \
            or "startCommand" in railway
        record(12, "railway.toml: builder + healthcheckPath = \"/health\"", bool(ok and builder_ok))
    if render:
        ok = re.search(r"type:\s*web", render) and re.search(r"healthCheckPath:\s*/health", render)
        plain_secret = re.search(r"key:\s*(AGENT_API_KEY|JWT_SECRET|OPENAI_API_KEY)\s*\n\s*value:",
                                 render)
        record(12, "render.yaml: web service + healthCheckPath /health", bool(ok))
        record(12, "render.yaml: secret không ghi thẳng giá trị (generateValue / sync: false)",
               not plain_secret, plain_secret.group(1) if plain_secret else "")
    start_cmds = [a for i, a in ins if i in ("CMD", "ENTRYPOINT")]
    railway_cmd = re.search(r'startCommand\s*=\s*"([^"]*)"', railway)
    port_ok = (start_cmds and "PORT" in start_cmds[-1]) or \
              (railway_cmd and "$PORT" in railway_cmd.group(1) and not render)
    record(12, "App lắng nghe theo biến PORT do platform cấp", bool(port_ok),
           "" if port_ok else "CMD trong Dockerfile đang cố định port — dùng ${PORT:-8000}")


# ─────────────────────────────────────────────────────────
# 2. Runtime — tự khởi động agent và gọi API thật
# ─────────────────────────────────────────────────────────
def _redis_ping(host, port, timeout=1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(b"PING\r\n")
            return s.recv(64).startswith(b"+PONG")
    except OSError:
        return False


def _docker_ready() -> tuple:
    if not shutil.which("docker"):
        return False, "không có lệnh docker"
    r = _run(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if r.returncode != 0:
        return False, "Docker daemon chưa chạy (mở Docker Desktop?)"
    return True, f"Docker {r.stdout.strip()}"


def _provide_redis(cli_url):
    """Tìm Redis cho bài test stateless. Trả về (redis_url | None, mô tả)."""
    url = cli_url or os.getenv("REDIS_URL")
    if url:
        parsed = urllib.parse.urlparse(url)
        if _redis_ping(parsed.hostname or "localhost", parsed.port or 6379):
            return url, f"dùng {url}"
        return None, f"không kết nối được {url}"

    port = _free_port()
    if shutil.which("redis-server"):
        proc = subprocess.Popen(
            ["redis-server", "--port", str(port), "--save", "", "--appendonly", "no"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _cleanup_actions.append(lambda: (proc.terminate(), proc.wait(5)))
        how = "redis-server tạm thời"
    elif _docker_ready()[0]:
        name = f"prod-check-redis-{port}"
        r = _run(["docker", "run", "-d", "--rm", "--name", name,
                  "-p", f"127.0.0.1:{port}:6379", "redis:7-alpine"], timeout=180)
        if r.returncode != 0:
            return None, f"docker run redis thất bại: {_last_lines(r.stderr, 1)}"
        _cleanup_actions.append(lambda: _run(["docker", "rm", "-f", name], timeout=30))
        how = "container redis:7-alpine tạm thời"
    else:
        return None, "không có REDIS_URL, redis-server hay Docker"

    for _ in range(40):
        if _redis_ping("127.0.0.1", port):
            return f"redis://127.0.0.1:{port}/0", how
        time.sleep(0.5)
    return None, f"{how} không phản hồi"


class Agent:
    """Chạy agent bằng uvicorn (giống CMD trong Dockerfile) trên một port trống."""

    def __init__(self, base, app, workers, env_overrides):
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = os.environ.copy()
        env.pop("REDIS_URL", None)
        env.update({"PYTHONPATH": base + os.pathsep + env.get("PYTHONPATH", ""),
                    "PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "PORT": str(self.port)})
        env.update(env_overrides)
        self.log = tempfile.NamedTemporaryFile(prefix="agent-check-", suffix=".log", delete=False)
        _cleanup_actions.append(lambda: os.unlink(self.log.name))
        cmd = [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1",
               "--port", str(self.port), "--workers", str(workers)]
        extra = {"start_new_session": True} if os.name != "nt" else {}
        self.proc = subprocess.Popen(cmd, cwd=base, env=env, stdout=self.log,
                                     stderr=subprocess.STDOUT, **extra)
        _cleanup_actions.append(self.kill)

    def wait_ready(self) -> tuple:
        """(True, "") khi GET /health trả 200; (False, lý do) nếu không."""
        deadline = time.time() + STARTUP_TIMEOUT
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False, f"process thoát với exit code {self.proc.returncode}"
            status = _http("GET", self.url + "/health", timeout=2)[0]
            if status == 200:
                return True, ""
            if status in (404, 405):
                return False, f"GET /health trả {status} — chưa có endpoint /health"
            time.sleep(0.3)
        return False, f"không trả /health 200 trong {STARTUP_TIMEOUT}s"

    def stop(self):
        if self.proc.poll() is None:
            if os.name == "nt":
                self.proc.terminate()
            else:
                self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
        self.kill()

    def kill(self):
        """Dọn sạch cả process cha lẫn các worker con."""
        if os.name != "nt":
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        elif self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            pass

    def log_text(self) -> str:
        self.log.flush()
        return _read(self.log.name)

    def log_tail(self, n=10) -> str:
        return _last_lines(self.log_text(), n) or "(không có log)"


class AskClient:
    """Gọi POST /ask. Hỗ trợ cả body JSON lẫn query param (?question=...)."""

    def __init__(self, url, style="json"):
        self.url = url
        self.style = style

    def ask(self, key, question="What is deployment?", timeout=15):
        headers = {"Content-Type": "application/json"}
        if key is not None:
            headers["X-API-Key"] = key
        if self.style == "query":
            return _http("POST", f"{self.url}/ask?question={urllib.parse.quote(question)}",
                         headers, b"", timeout)
        body = json.dumps({"question": question, "user_id": "prod-check"})
        return _http("POST", f"{self.url}/ask", headers, body, timeout)


def _start(base, args, workers, env, criterion, label):
    agent = Agent(base, args.app, workers, env)
    ok, reason = agent.wait_ready()
    if ok:
        return agent
    record(criterion, f"{label}: agent khởi động được", False,
           f"{reason}. Log cuối:\n{_indent(agent.log_tail())}")
    agent.kill()
    return None


def _check_json_logs(text: str):
    good, bad = 0, []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            if isinstance(json.loads(line), dict):
                good += 1
        except ValueError:
            bad.append(line)
    criterion, name = RT_LOGS
    if bad:
        record(criterion, name, False,
               f"{len(bad)} dòng log bắt đầu bằng '{{' nhưng không parse được, vd: {bad[0][:100]}")
    elif good == 0:
        record(criterion, name, False, "không thấy dòng log JSON nào")
    else:
        record(criterion, name, True, f"{good} dòng JSON")


def runtime_checks(base: str, args):
    module_file = os.path.join(base, *args.app.split(":")[0].split(".")) + ".py"
    if not os.path.exists(module_file):
        record_missing(RUNTIME_CHECKS, False,
                       f"không chạy được: không có {os.path.relpath(module_file, base)}")
        return
    missing = [m for m in ("fastapi", "uvicorn") if importlib.util.find_spec(m) is None]
    if missing:
        record_missing(RUNTIME_CHECKS, False,
                       f"không chạy được: thiếu {', '.join(missing)} — chạy: pip install -r requirements.txt")
        return

    progress("Chuẩn bị Redis cho bài test stateless...")
    redis_url, redis_note = _provide_redis(args.redis_url)
    base_env = {"ENVIRONMENT": "staging", "DEBUG": "false",
                "RATE_LIMIT_PER_MINUTE": str(RATE_LIMIT_FOR_TEST),
                "DAILY_BUDGET_USD": "1000", "MONTHLY_BUDGET_USD": "1000"}
    if redis_url:
        base_env["REDIS_URL"] = redis_url

    # ── Lần chạy 1: health, ready, auth, rate limit, log ──
    progress(f"Khởi động agent: uvicorn {args.app} --workers {args.workers} (Redis: {redis_note})")
    key = "check-" + secrets.token_hex(8)
    env_main = {**base_env, "AGENT_API_KEY": key}
    agent = _start(base, args, args.workers, env_main, 4, "Khởi động")
    if agent is None:
        record_missing(RUNTIME_CHECKS, False,
                       "chưa chạy được — agent phải khởi động và GET /health trả 200 trước")
        return
    record(*RT_HEALTH, True, f"uvicorn --workers {args.workers}")
    status = _http("GET", agent.url + "/ready")[0]
    record(*RT_READY, status == 200, "" if status == 200 else f"HTTP {status}")

    client = AskClient(agent.url)
    no_key = client.ask(None)[0]
    wrong_key = client.ask("wrong-" + secrets.token_hex(4))[0]
    ok = no_key in (401, 403) and wrong_key in (401, 403)
    record(*RT_AUTH_BAD, ok,
           "" if ok else f"thiếu key → {no_key}, sai key → {wrong_key} (mong đợi 401)")
    status, _, body = client.ask(key)
    if status == 422:            # có thể /ask nhận question qua query param
        client.style = "query"
        status, _, body = client.ask(key)
    key_ok = status == 200
    record(*RT_AUTH_OK, key_ok, "" if key_ok else f"HTTP {status}: {body[:120]}")

    successes = 1 if key_ok else 0
    got_429, retry_after, codes = False, False, []
    for _ in range(MAX_BURST):
        status, headers, _ = client.ask(key)
        codes.append(status)
        if status == 200:
            successes += 1
        elif status == 429:
            got_429, retry_after = True, "retry-after" in headers
            break
    if got_429:
        detail = f"429 sau {successes} request thành công (RATE_LIMIT_PER_MINUTE={RATE_LIMIT_FOR_TEST})"
        if not retry_after:
            detail += "; nên thêm header Retry-After"
    else:
        seen = ", ".join(f"{c}×{codes.count(c)}" for c in dict.fromkeys(codes))
        detail = f"gửi {MAX_BURST} request liên tiếp không thấy 429 ({seen})"
    record(*RT_RATE, got_429, detail)

    # Config: agent phải dùng giá trị từ env (API key ngẫu nhiên, giới hạn 5/phút)
    env_ok = key_ok and got_429 and successes <= RATE_LIMIT_FOR_TEST * args.workers
    record(*RT_ENV, env_ok,
           "" if env_ok else
           f"key ngẫu nhiên → {'200' if key_ok else 'bị từ chối'}, {successes} request trước khi bị chặn")
    _check_json_logs(agent.log_text())
    agent.stop()

    # ── Lần chạy 2: restart — state rate limit phải còn (nằm trong Redis) ──
    _, name = RT_STATELESS
    if not redis_url:
        record(*RT_STATELESS, None, f"chưa kiểm tra được — {redis_note}. Cài Docker / redis-server hoặc đặt REDIS_URL")
    elif not got_429:
        record(*RT_STATELESS, False, "cần rate limit hoạt động mới kiểm tra được")
    else:
        progress("Restart agent để kiểm tra stateless...")
        agent = _start(base, args, args.workers, env_main, 7, "Stateless")
        if agent:
            status = AskClient(agent.url, client.style).ask(key)[0]
            record(*RT_STATELESS, status == 429, "" if status == 429 else
                   f"sau restart trả {status} → state đang nằm trong memory của process")
            agent.stop()

    # ── Lần chạy 3: Redis mất kết nối → /ready phải 503, /health vẫn 200 ──
    progress("Kiểm tra readiness khi Redis mất kết nối...")
    env_down = {**base_env, "AGENT_API_KEY": "check-" + secrets.token_hex(8),
                "REDIS_URL": f"redis://127.0.0.1:{_free_port()}/0"}
    agent = _start(base, args, 1, env_down, 5, "Readiness (Redis down)")
    if agent:
        status = _http("GET", agent.url + "/ready", timeout=15)[0]
        record(*RT_READY_DOWN, status == 503,
               "" if status == 503 else f"/ready trả {status} — readiness nên kiểm tra kết nối Redis")
        agent.stop()

    # ── Lần chạy 4: cost guard với budget rất nhỏ ──
    progress("Kiểm tra cost guard (budget ≈ 0)...")
    key_cost = "check-" + secrets.token_hex(8)
    env_cost = {**base_env, "AGENT_API_KEY": key_cost, "RATE_LIMIT_PER_MINUTE": "1000",
                "DAILY_BUDGET_USD": "0.000001", "MONTHLY_BUDGET_USD": "0.000001"}
    agent = _start(base, args, 1, env_cost, 8, "Cost guard")
    if agent:
        cost_client = AskClient(agent.url, client.style)
        codes = []
        for _ in range(10):
            codes.append(cost_client.ask(key_cost)[0])
            if codes[-1] == 402:
                break
        ok = 402 in codes
        record(*RT_COST, ok,
               f"402 ở request thứ {len(codes)}" if ok else f"budget 0.000001 USD nhưng 10 request trả {codes}")
        agent.stop()

    # ── Lần chạy 5: graceful shutdown ──
    if os.name == "nt":
        record(*RT_GRACEFUL, None, "chưa kiểm tra được — Windows không gửi được SIGTERM (phần Docker sẽ kiểm tra)")
        return
    progress("Kiểm tra graceful shutdown (SIGTERM)...")
    key_grace = "check-" + secrets.token_hex(8)
    env_grace = {**base_env, "AGENT_API_KEY": key_grace, "RATE_LIMIT_PER_MINUTE": "1000"}
    agent = _start(base, args, args.workers, env_grace, 11, "Graceful shutdown")
    if agent is None:
        return
    holder = {}
    grace_client = AskClient(agent.url, client.style)
    worker = threading.Thread(target=lambda: holder.update(status=grace_client.ask(key_grace, timeout=30)[0]))
    worker.start()
    time.sleep(0.05)                       # để request kịp tới agent
    agent.proc.send_signal(signal.SIGTERM)
    try:
        agent.proc.wait(SHUTDOWN_TIMEOUT)
        exited = True
    except subprocess.TimeoutExpired:
        exited = False
    worker.join(30)
    in_flight = holder.get("status")
    if not exited:
        detail = (f"process không thoát sau {SHUTDOWN_TIMEOUT}s — handler SIGTERM tự viết có chặn "
                  "mất handler của uvicorn không?")
    elif in_flight != 200:
        detail = f"request đang chạy bị cắt ngang (nhận {in_flight})"
    else:
        detail = f"exit code {agent.proc.returncode}"
    record(*RT_GRACEFUL, exited and in_flight == 200, detail)
    agent.kill()


# ─────────────────────────────────────────────────────────
# 3. Docker — build image, đo kích thước, chạy container
# ─────────────────────────────────────────────────────────
def docker_checks(base: str):
    ok, note = _docker_ready()
    names = DOCKER_CHECKS
    if not ok:
        record_missing(DOCKER_CHECKS, None, f"chưa kiểm tra được — {note}")
        return

    tag = f"prod-check:{secrets.token_hex(4)}"
    progress(f"Build Docker image {tag} ({note}) — lần đầu có thể mất vài phút...")
    started = time.time()
    r = _run(["docker", "build", "-t", tag, "."], timeout=DOCKER_BUILD_TIMEOUT, cwd=base)
    if r.returncode != 0:
        out = r.stderr or r.stdout
        errors = [l.strip() for l in out.splitlines() if "ERROR" in l or "error:" in l.lower()]
        msg = "\n".join(errors[-3:]) if errors else _last_lines(out, 10)
        record(1, names[0][1], False, "Lỗi:\n" + _indent(msg))
        for c, n in names[1:]:
            record(c, n, False, "không có image để kiểm tra")
        return
    _cleanup_actions.append(lambda: _run(["docker", "rmi", "-f", tag], timeout=60))
    record(1, names[0][1], True, f"{time.time() - started:.0f}s")

    r = _run(["docker", "image", "inspect", "--format", "{{.Size}}", tag], timeout=30)
    try:
        size_mb = int(r.stdout.strip()) / 1024 / 1024
        record(1, names[1][1], size_mb < MAX_IMAGE_MB, f"{size_mb:.0f} MB")
    except ValueError:
        record(1, names[1][1], False, f"không đọc được kích thước: {r.stderr.strip()[:100]}")

    container = f"prod-check-{secrets.token_hex(4)}"
    port = _free_port()
    r = _run(["docker", "run", "-d", "--name", container, "-p", f"127.0.0.1:{port}:8000",
              "-e", "PORT=8000", "-e", "ENVIRONMENT=staging",
              "-e", f"AGENT_API_KEY=check-{secrets.token_hex(8)}", tag], timeout=60)
    if r.returncode != 0:
        record(1, names[2][1], False, _last_lines(r.stderr, 3))
        record(11, names[3][1], False, "container không chạy được")
        return
    _cleanup_actions.append(lambda: _run(["docker", "rm", "-f", container], timeout=30))

    healthy, deadline = False, time.time() + 45
    while time.time() < deadline:
        if _http("GET", f"http://127.0.0.1:{port}/health", timeout=2)[0] == 200:
            healthy = True
            break
        state = _run(["docker", "inspect", "-f", "{{.State.Running}}", container], timeout=10)
        if state.stdout.strip() == "false":
            break
        time.sleep(1)
    if not healthy:
        logs = _run(["docker", "logs", "--tail", "12", container], timeout=15)
        record(1, names[2][1], False,
               "container không trả /health 200. Log cuối:\n" + _indent(_last_lines(logs.stdout + logs.stderr, 12)))
        record(11, names[3][1], False, "container không chạy được")
        return
    record(1, names[2][1], True)

    started = time.time()
    _run(["docker", "stop", "-t", str(DOCKER_STOP_GRACE + 5), container], timeout=60)
    elapsed = time.time() - started
    code = _run(["docker", "inspect", "-f", "{{.State.ExitCode}}", container], timeout=10).stdout.strip()
    graceful = code == "0" and elapsed < DOCKER_STOP_GRACE
    record(11, names[3][1], graceful,
           f"exit code {code}, {elapsed:.1f}s" + ("" if graceful else
           " — 137 nghĩa là bị SIGKILL: app không xử lý SIGTERM hoặc uvicorn không phải PID 1"))


def public_url_check(url: str):
    status, _, body = _http("GET", url.rstrip("/") + "/health", timeout=15)
    record(12, f"Public URL: GET {url.rstrip('/')}/health → 200", status == 200,
           "" if status == 200 else f"HTTP {status}: {body[:100]}")


# ─────────────────────────────────────────────────────────
# Báo cáo
# ─────────────────────────────────────────────────────────
def report(args) -> bool:
    print("\n" + "=" * 64)
    print("  Production Readiness Check — Day 14 Lab")
    print("=" * 64)
    summary = []
    for idx, title in enumerate(CRITERIA, start=1):
        items = [r for r in RESULTS if r["criterion"] == idx]
        if any(r["passed"] is False for r in items):
            status = "❌"
        elif any(r["passed"] is None for r in items) or not items:
            status = "⚠️ "
        else:
            status = "✅"
        summary.append((status, title))
        print(f"\n{status} {idx}. {title}")
        for r in items:
            icon = "⚠️ " if r["passed"] is None else ("✅" if r["passed"] else "❌")
            detail = r["detail"]
            if detail and "\n" in detail:
                print(f"     {icon} {r['name']} — {detail.splitlines()[0]}")
                print("\n".join("     " + line for line in detail.splitlines()[1:]))
            else:
                print(f"     {icon} {r['name']}" + (f" — {detail}" if detail else ""))

    failed = [t for s, t in summary if s == "❌"]
    partial = [t for s, t in summary if s.startswith("⚠️")]
    n_pass = sum(1 for s, _ in summary if s == "✅")
    checks_pass = sum(1 for r in RESULTS if r["passed"] is True)
    checks_total = sum(1 for r in RESULTS if r["passed"] is not None)

    print("\n" + "=" * 64)
    print("  📋 Checklist Deliverable")
    for status, title in summary:
        print(f"     {status} {title}")
    print("-" * 64)
    print(f"  Result: {n_pass}/{len(CRITERIA)} tiêu chí đạt · {checks_pass}/{checks_total} checks passed")
    if partial:
        print(f"  ⚠️  Chưa kiểm tra được: {', '.join(partial)}")
    if args.static_only or args.skip_docker:
        print("  ℹ️  Đang chạy chế độ nhanh — kết quả chấm phải chạy đầy đủ (không --static-only / --skip-docker)")
    if failed:
        print(f"  ❌ CHƯA ĐẠT — sửa {len(failed)} tiêu chí ❌ ở trên.")
    elif partial:
        print("  ❌ CHƯA ĐẠT — còn tiêu chí chưa kiểm tra được (⚠️). Chạy đầy đủ trên máy có Docker.")
    else:
        print("  🎉 PASS 12/12 — PRODUCTION READY! Deploy nào!")
    print("=" * 64 + "\n")
    return not failed and not partial


def main():
    parser = argparse.ArgumentParser(description="Kiểm tra production readiness theo checklist Day 14")
    parser.add_argument("--static-only", action="store_true", help="Chỉ kiểm tra tĩnh")
    parser.add_argument("--skip-docker", action="store_true", help="Bỏ phần build/chạy Docker image")
    parser.add_argument("--app", default="app.main:app", help="Module:biến ASGI app (mặc định app.main:app)")
    parser.add_argument("--workers", type=int, default=2,
                        help="Số uvicorn worker khi chạy thử (mặc định 2, giống Dockerfile)")
    parser.add_argument("--redis-url", help="Redis dùng cho bài test stateless")
    parser.add_argument("--public-url", help="URL đã deploy, vd https://your-app.railway.app")
    args = parser.parse_args()

    base = os.path.dirname(os.path.abspath(__file__))
    print(f"\n🔎 Kiểm tra {base}")
    static_checks(base)
    if args.static_only:
        record_missing(RUNTIME_CHECKS + DOCKER_CHECKS, None, "chưa chạy (--static-only)")
    else:
        runtime_checks(base, args)
        record_missing(RUNTIME_CHECKS, False, "không chạy được do bước trước bị lỗi")
        if args.skip_docker:
            record_missing(DOCKER_CHECKS, None, "chưa chạy (--skip-docker)")
        else:
            docker_checks(base)
    if args.public_url:
        public_url_check(args.public_url)
    ready = report(args)
    _cleanup()
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
