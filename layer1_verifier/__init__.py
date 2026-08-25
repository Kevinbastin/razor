"""
Agent Transaction Risk Layer — Layer 1: Mandate Verifier

Deterministic checks against mandate scope:
- Amount ceiling
- Category match
- Time window validity
- Lifecycle state (active/paused/revoked)
- Cumulative spend tracking
- Cadence enforcement

Runs BEFORE any ML layer. Fail-closed on malformed/missing data.
"""
