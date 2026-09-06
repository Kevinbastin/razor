# Architecture

## Runtime flow

The service treats a transaction as independently explainable decisions. Layer 1 receives a transaction, mandate snapshot, identity registry, and recent mandate timestamps. It returns a verdict, failed checks, evidence, and mandate snapshot. Layer 2 converts the session-event timeline and prior mandate history into fixed feature columns, then returns a risk score, behavioural verdict, and ranked factors. Layer 3 compares the stated mandate purpose with a cart description, evaluates immediate external-source provenance, scans ingested content for injection structures, and evaluates its escalation triple using only earlier mandate transactions. Layer 4 combines those outputs with the timeline and produces a packet, disposition, and deterministic liability route.

## Decision precedence

`fraud-contest` wins when Layer 1 V6 identity attestation fails or Layer 2 has a replay signal. Next, any Layer 1 scope failure becomes `merchant-should-accept`. A valid mandate with Layer 3 flagged becomes `escalate-to-provider`; otherwise it is `merchant-defensible`. This is rules code, not an LLM decision. `suspicious` Layer 2 behaviour with clear authority and intent becomes `pending re-authorization`, preserving a recovery path for legitimate traffic.

## Evidence and narratives

Packets contain the transaction, mandate snapshot, all three trails, full ordered session timeline, liability determination, disposition, and action outcome. Narrative generation is separate from routing: the LLM receives the packet as its sole source, is instructed not to infer missing facts, and the exact prompt is stored with the output. Without credentials, the packet records `not_generated` rather than fabricating prose.

## External actions

The Razorpay wrapper uses environment credentials, a local idempotency key/cache per state-changing request, exponential retry for transient statuses, and a circuit breaker. TPAP mandate base URL is configurable because the mandate interface is bank/account provisioned; disputes use the standard Razorpay host. Auto-pause is attempted only for an active mandate with `fraud-contest` or `escalate-to-provider`, and only when the caller supplies required TPAP payload fields.

## Observability and quality gates

Decision boundaries emit structured `layer1_decision` through `layer4_decision` events. `observability.metrics` maintains rolling in-memory verdict and false-positive counters. The frozen mandate split prevents leakage. `check_layer2_regression.py` rejects candidate models that reduce frozen-holdout precision or recall, while `tests/adversarial/` must pass before an intent-layer change is accepted. `tests/chaos/` verifies malformed inputs and out-of-order events fail closed.
