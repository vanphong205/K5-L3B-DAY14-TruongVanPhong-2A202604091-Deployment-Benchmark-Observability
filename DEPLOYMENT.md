# Deployment Information

## Public URL
https://k5-day14-agent.up.railway.app

## Platform
Railway (Optionally deployable to Render / Cloud Run)

## Test Commands

### 1. Health Check (Liveness Probe)
```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://k5-day14-agent.up.railway.app/health
# Expected: 200
```

### 2. Readiness Check (Readiness Probe)
```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://k5-day14-agent.up.railway.app/ready
# Expected: 200
```

### 3. API Test Without Key (Authentication Required)
```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://k5-day14-agent.up.railway.app/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Hello"}'
# Expected: 401
```

### 4. API Test With Valid API Key
```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://k5-day14-agent.up.railway.app/ask \
  -H "X-API-Key: your-secret-api-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "student-test", "question": "Explain Docker multi-stage builds"}'
# Expected: 200
```

### 5. Rate Limiting Test
```bash
for i in {1..15}; do
  curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://k5-day14-agent.up.railway.app/ask \
    -H "X-API-Key: your-secret-api-key" \
    -H "Content-Type: application/json" \
    -d '{"question": "test"}';
done
# Expected: 200 (first 10 requests) then 429 (rate limit exceeded)
```

## Environment Variables Set
- `PORT`: `8000`
- `ENVIRONMENT`: `production`
- `REDIS_URL`: `redis://default:password@roundhouse.proxy.rlwy.net:12345` (kết nối Redis instance trên Railway/Render)
- `AGENT_API_KEY`: `your-secret-api-key`
- `RATE_LIMIT_PER_MINUTE`: `10`
- `DAILY_BUDGET_USD`: `10.0`

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
