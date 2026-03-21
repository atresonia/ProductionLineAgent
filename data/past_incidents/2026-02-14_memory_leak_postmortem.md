# Post-Mortem: API Memory Leak — Order Processing
**Date:** 2026-02-14
**Severity:** SEV-2
**Duration:** 34 minutes
**On-call:** Sarah Kim

## Summary
The api service experienced a gradual memory leak starting after the v1.8 deploy. Memory grew from 180MB to 1.2GB over 25 minutes, causing OOM restarts and degraded /checkout latency (p95 4,200ms). Root cause: a response caching bug where full response bodies were being stored in an in-process dict without eviction.

## Root Cause
`_response_cache` dict in api/cache.py was growing unbounded. Each /checkout response (averaging 2KB) was stored keyed by session_id but never evicted. 250 requests/min × 2KB × 25 min = ~750MB growth. Confirmed by heap dump showing `_response_cache` holding 380,000 entries.

## Fix Applied
- Restarted api service (immediate relief — memory reset to baseline)
- Deployed hotfix v1.8.1 with LRU cache (max 1000 entries, 5-min TTL)

## Key Takeaways
- Memory leak symptoms: steady linear growth in get_memory_trend, latency increasing proportionally with memory
- Restart clears the symptom; the bug requires a code fix
- Always check if memory growth correlates with a specific endpoint's traffic pattern
