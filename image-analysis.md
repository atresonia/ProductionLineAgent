# Multi-Modal Workflow
## Step 1: Inject the issue
Run `python chaos.py bad_deploy`. API returns 500s on /checkout.
## Step 2: The agent detects the anomaly automatically
The agent monitor (running in a loop every 5 second), reads the log files and sees that the API error rate just crossed 85%. It prints
```
INCIDENT DETECTED
  • High error rate on api: 87% (26/30 requests failing in last 3m)
  • Frontend reporting 502s upstream from api
```

## Step 3: Agent writes a plan
Before calling any tools, the agent outputs its investigation strategy:
```
Plan: I will first check the api error rate to quantify impact, then read 
recent api logs for error details. I'll check for stack traces to find the 
crash location, capture a dashboard to see the spike shape and timing, read 
the deployment config for misconfigurations, and check deploy history to 
correlate with recent changes.
```
## Step 4: Agent investigates (tool calling)
```
→ tool: get_error_rate       {"service": "api"}
← result: 87% error rate, 26/30 failing, all on /checkout, "Payment gateway connection refused"

→ tool: read_logs            {"service": "api", "lines": 40}  
← result: [stream of JSON log lines showing 500s with "PAYMENT_GATEWAY_URL misconfigured"]

→ tool: parse_stack_traces   {"service": "api"}
← result: [stack trace pointing to the checkout handler]

→ tool: capture_dashboard    {"window_minutes": 15}
← result: Dashboard saved. Error rate cliff from 0.3% → 87% at 14:32.
  [image attached — Claude analyzing visual signal]
```

Now, the agent might output something like "The dashboard shows a cliff in error rate beginning at 14:32, not gradual degradation. 
```
→ tool: read_config_file     {"filename": "deployment.yaml"}
← result: [YAML with PAYMENT_GATEWAY_URL pointing to payments-legacy.internal — stale]

→ tool: get_deploy_history   {}
← result: [startup event showing v2.1 deployed at 14:31:58]
```
## Step 5: Agent alerts Slack
```
→ tool: send_slack_alert     {"message": "SEV-2 | api /checkout failing | Root cause: 
                              PAYMENT_GATEWAY_URL points to decommissioned endpoint, 
                              introduced in v2.1 deploy", "severity": "critical"}
```
## Step 6: Agent requests remediation approval
```
╔══════════════════════════════════════════╗
║  REMEDIATION REQUEST                     ║
║                                          ║
║  Action  : rollback                      ║
║  Service : api                           ║
║  Reason  : Revert to v2.0 to restore     ║
║            working PAYMENT_GATEWAY_URL   ║
║                                          ║
║  Approve? [y/N]                          ║
╚══════════════════════════════════════════╝
You, as the human-in-the-loop, type `y`.
```
## Step 7: System recovers
```
✅ Resolved in 2m 14s | Rollback to v2.0 | Post-mortem: postmortems/2026-03-21_143215_incident.md
```
## Step 8: Agent writes the post-mortem
