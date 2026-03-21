# Runbook: Payment Gateway Failures

**Scope:** /checkout returning 500s, payment gateway unreachable
**Owner:** Payments Team (Priya Sharma)
**Last updated:** 2026-03-05

## Quick Diagnosis

1. Check PAYMENT_GATEWAY_URL in `configs/deployment.yaml`
2. Verify the URL matches the current gateway endpoint: `https://payments.internal/v2/charge`
3. Check api logs for the exact error: `search_logs("api", "PAYMENT_GATEWAY_URL")`
4. Verify gateway is reachable: the gateway returns 200 on GET /health

## Common Causes

### Stale config after gateway migration
- Symptom: 500s on /checkout, logs show `PAYMENT_GATEWAY_URL` error, gateway itself is healthy
- Fix: Roll back the deploy that introduced the stale config, OR update the env var and **restart the service** (env vars are not hot-reloaded)
- IMPORTANT: Updating the config alone is not enough — you must restart the service

### Gateway outage (external)
- Symptom: 500s on /checkout, gateway /health returns non-200
- Fix: Enable payment fallback mode (set PAYMENT_FALLBACK=true) — this serves a "try again later" response instead of erroring

### Rate limiting
- Symptom: 429s from gateway, api logs show "rate limit exceeded"
- Fix: Scale down checkout traffic or contact gateway support for limit increase

## Rollback Procedure

```
# Immediate: rollback the deploy
kubectl rollout undo deployment/api

# Verify
kubectl rollout status deployment/api
# Check error rate drops below 5%
```

## Escalation
- Page Priya Sharma for any payment gateway issue lasting > 5 minutes
- If gateway SLA breach suspected, contact Stripe/payment provider support
