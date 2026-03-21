# Runbook: Database Connection Issues

**Scope:** api returning 503s, DB unreachable
**Owner:** Platform Team
**Last updated:** 2026-01-18

## Quick Diagnosis

1. Check api logs for DB connection errors: `search_logs("api", "OperationalError")`
2. Check if all endpoints are affected (DB down) or just data-heavy ones (pool exhaustion)
3. Verify DB container status

## Common Causes

### DB container is down
- Symptom: 503 on all api endpoints, logs show `psycopg2.OperationalError: could not connect`
- Fix: Restart DB container and wait for health check to pass

### Connection pool exhausted
- Symptom: Slow queries piling up, pool timeout errors in logs
- Fix: Restart api service to reset connections, then investigate slow query

### Slow queries blocking the pool
- Symptom: High latency (p95 > 2000ms), not errors; pool eventually backs up
- Fix: Identify the slow query from DB logs, add index or optimize

## Recovery Steps

```bash
# 1. Check DB is up
docker compose ps db

# 2. Restart DB if down
docker compose restart db

# 3. Check api can connect
curl http://localhost:8000/health
```

## Containerized Notes
- DB has a health check: `pg_isready -U resolve`
- api service has `depends_on: db: condition: service_healthy` — but this only applies at startup
- If DB crashes mid-run, api will not automatically reconnect until restarted

## Escalation
- If DB is corrupted or data loss suspected, do NOT restart — page SRE lead immediately
- Check DB volume mounts before any destructive recovery
