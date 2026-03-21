# Post-Mortem: Payment Gateway Config Drift
**Date:** 2026-03-03
**Severity:** SEV-2
**Duration:** 11 minutes (02:14–02:25 UTC)
**On-call:** Marcus Chen

## Summary
/checkout returned 500s for 11 minutes due to a stale PAYMENT_GATEWAY_URL in deployment.yaml. The payment gateway had been migrated to a new endpoint the previous week. The Kubernetes secret was updated but the deployment config was not. When v2.1 was deployed, it picked up the stale URL from the config file.

## Timeline
- 02:10 — v2.1 deployed to production
- 02:14 — PagerDuty fires: error rate 78% on /checkout
- 02:14 — Marcus joins incident call
- 02:16 — Priya identifies config drift in PAYMENT_GATEWAY_URL
- 02:21 — Rollback to v2.0 initiated
- 02:22 — Error rate drops to ~0%
- 02:25 — Incident resolved

## Root Cause
PAYMENT_GATEWAY_URL in deployment.yaml pointed to `https://legacy-payments.internal/v1/charge` (decommissioned endpoint). The correct URL is `https://payments.internal/v2/charge`. The stale config was introduced when the v2.1 deploy went out without a config validation step.

## Contributing Factors
1. No config validation step in the deploy pipeline
2. PAYMENT_GATEWAY_URL was in two places (secret + config) that could drift
3. On-call rotation did not include the payments team lead

## Remediation Applied
- Rolled back api service to v2.0 (which had the correct URL baked in at build time)
- Verified payment gateway connectivity post-rollback

## Prevention
- [ ] Add config validation step to CI/CD: cross-check env vars in deployment.yaml against current secrets registry
- [ ] Add integration test: POST /checkout → assert 200 (pings real payment gateway URL from config)
- [ ] Ensure payments team lead is available during deploys that touch payment config
- [ ] Single source of truth for PAYMENT_GATEWAY_URL: read from secret only, remove from config file

## Lessons Learned
- When restarting a service, verify env var values before restarting — don't just restart blindly
- Stale config files are a silent failure mode: the service starts, the URL looks valid, but the endpoint is dead
- Cross-referencing the Slack #deployments channel (Priya's warning message from the day before) would have surfaced this immediately
