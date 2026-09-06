# Threat model

## Protected outcome

The system protects a user’s delegated payment goal, not merely their payment credential. The protected decision is whether a cart and beneficiary still serve the mandate’s stated purpose at the time payment is initiated.

## Assumptions

The payment credential, mandate identifier, and ordinary agent session may all be valid. External content read by the agent is untrusted. Razorpay remains the payment and dispute system of record; this service observes structured transaction/session inputs and routes a risk response.

## In-scope attacks

- Credential replay and spoofed agent identity (A1/A2).
- Scope breaches: amount, category, time, lifecycle, cumulative spend, cadence (A3–A5).
- Goal hijack through indirect prompt injection, including SEO-poisoned instructions, hidden imperative text, and embedded payment payloads (A6).

## Controls and residual risk

Layer 1 constrains authority; Layer 2 detects abnormal execution; Layer 3 detects outcome divergence and known injection structures; Layer 4 records evidence and applies a deterministic response. The design does not claim to solve every semantic or multi-turn injection. Obfuscation, adversarial paraphrase, compromised trusted sources, and missing/incorrect merchant data remain residual risks requiring human review, real-traffic validation, and defense in depth.
