# Runbook: Memory Leak Response

**Scope:** Service memory growing unbounded, OOM risk
**Owner:** Platform Team
**Last updated:** 2026-02-20

## Detection

Memory leak indicators (from get_memory_trend):
- Delta > 50MB in 5 minutes: investigate
- Delta > 80MB in 5 minutes: page on-call
- Service memory > 800MB: restart immediately to prevent OOM

## Diagnosis Steps

1. `get_memory_trend("api", window_minutes=15)` — confirm growth is linear and sustained
2. `get_latency_stats("api")` — check if latency is also degrading (OOM pressure slows GC)
3. `search_logs("api", "memory")` — look for any memory warning events
4. Check if growth correlates with a specific endpoint — leak is usually tied to one code path
5. Review recent deploys: `get_deploy_history()` — leak usually starts after a deploy

## Immediate Response

### Step 1: Restart the service (buys 15–30 minutes)
- Restart resets the in-process memory to baseline immediately
- Use `execute_remediation("restart", "api")`
- Memory will grow again if the underlying bug isn't fixed — this is a temporary measure

### Step 2: Reduce traffic
- Scale down load gen or enable rate limiting to slow the leak rate

### Step 3: Root cause
- Heap analysis (if accessible): look for large dicts/lists that shouldn't be growing
- Common culprits: unbounded caches, event listeners not cleaned up, large objects held in request context

## Prevention
- Add memory limit to deployment config: `resources.limits.memory: 512Mi`
- Add alerting at 70% of memory limit
- Code review: any new dict/list that grows with requests needs an eviction strategy

## Note on Containerized Services
If running in Docker/Kubernetes:
- Container restart ≠ service restart — it rebuilds the in-process state
- In the current demo environment, `execute_remediation("restart")` writes "none" to the chaos flag, which clears the leak simulation
