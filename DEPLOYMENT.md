# Deployment Information

## Public URL
https://k5-l3b-day14-truongvanphong-2a202604091.onrender.com

## Platform
Render (Docker Web Service + Render Key Value / Redis)

## Test Commands

### 1. Health Check (Liveness Probe)
```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://k5-l3b-day14-truongvanphong-2a202604091.onrender.com/health
# Expected: 200
```

### 2. Readiness Check (Readiness Probe)
```bash
curl -sS -o /dev/null -w "%{http_code}\n" https://k5-l3b-day14-truongvanphong-2a202604091.onrender.com/ready
# Expected: 200
```

### 3. API Test Without Key (Authentication Required)
```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://k5-l3b-day14-truongvanphong-2a202604091.onrender.com/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"Hello"}'
# Expected: 401
```

### 4. API Test With Valid API Key
```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://k5-l3b-day14-truongvanphong-2a202604091.onrender.com/ask \
  -H "X-API-Key: $AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "student-test", "question": "Explain Docker multi-stage builds"}'
# Expected: 200
```

### 5. Rate Limiting Test
```bash
for i in {1..15}; do
  curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://k5-l3b-day14-truongvanphong-2a202604091.onrender.com/ask \
    -H "X-API-Key: $AGENT_API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"question": "test"}';
done
# Expected: 200 (first 10 requests) then 429 (rate limit exceeded)
```

## Environment Variables Set
- `PORT`: auto-injected by Render
- `ENVIRONMENT`: `production`
- `REDIS_URL`: `<Render Internal Key Value URL (set on Render Dashboard)>`
- `AGENT_API_KEY`: `<set on Render Dashboard, not stored in git>`
- `RATE_LIMIT_PER_MINUTE`: `10`
- `DAILY_BUDGET_USD`: `10.0`

## Screenshots
- [Deployment dashboard](screenshots/dashboard.png)
- [Service running](screenshots/running.png)
- [Test results](screenshots/test.png)
