# Operations runbook

This project is a demo-grade service with fail-closed decision logic. Do not use this document as a substitute for an incident-management policy in production.

## A false-positive spike

1. Inspect the structured `layer1_decision` through `layer4_decision` events and the metrics endpoint to identify the responsible layer or signal.
2. Do not lower thresholds ad hoc. Preserve the evidence packets and label reviewed transactions.
3. If I3 or I2 is the source, route affected transactions to step-up re-authorization while the rule is reviewed. If Layer 2 is implicated, evaluate a candidate model on the unchanged frozen holdout before promotion.
4. Record the decision and owner in the reviewer audit trail; roll back a model by restoring the previous versioned artifact.

## Razorpay outage or repeated action failures

1. The client retries transient failures and opens its circuit after repeated failures. Treat an open circuit as a failed action, never as a successful pause/contest.
2. Keep the locally generated evidence packet and queue the required action for manual retry after Razorpay recovers.
3. Verify the mandate/dispute state directly in test mode before retrying. Reuse the idempotency key for the same business action.

## Suspected rogue-agent incident

1. Preserve the Layer 1–3 trails, session timeline, mandate snapshot, and source-exposure evidence.
2. For active mandates with `fraud-contest` or `escalate-to-provider`, pause the mandate after verifying the result.
3. Notify the responsible reviewer, perform a human evidence review, then accept or contest the dispute using the packet.
4. Add a minimized, sanitized regression case to `tests/adversarial/` after the incident review; do not add production PII to the repository.

## Retention

Set `AUDIT_RETENTION_DAYS` (default 90). Startup applies retention to the local SQLite demonstration store. Production requires policy-approved retention, encrypted managed storage, access controls, and deletion verification.
