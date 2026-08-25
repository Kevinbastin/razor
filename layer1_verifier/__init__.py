"""
Layer 1 — Mandate Verifier (Deterministic)

Eight checks, each a pure function, each producing structured evidence.
Runs before any ML layer — cheap, explainable, fail-closed.

Checks:
    V1  amount_ceiling     — transaction amount vs. mandate ceiling (max/exact)
    V2  category_scope     — merchant MCC within mandate's permitted categories
    V3  time_window        — transaction hour within mandate's approved window
    V4  lifecycle_state    — reject if mandate is paused/revoked/expired
    V5  umn_integrity      — well-formed UMN, exists in mandate registry
    V6  key_attestation    — signing key matches agent registered for mandate
    V7  cumulative_spend   — running spend vs. mandate budget
    V8  cadence_compliance — transaction frequency vs. mandate's recurring rule

Design Principles (from CLAUDE.md):
    - Fail closed: malformed/missing data → "fail" verdict, never silent pass
    - Structured JSON reason trail (Layer 4 consumes this)
    - No ML, no network calls, no side effects
"""

from layer1_verifier.checks import (
    check_v1_amount_ceiling,
    check_v2_category_scope,
    check_v3_time_window,
    check_v4_lifecycle_state,
    check_v5_umn_integrity,
    check_v6_key_attestation,
    check_v7_cumulative_spend,
    check_v8_cadence_compliance,
    verify_transaction,
)

__all__ = [
    "check_v1_amount_ceiling",
    "check_v2_category_scope",
    "check_v3_time_window",
    "check_v4_lifecycle_state",
    "check_v5_umn_integrity",
    "check_v6_key_attestation",
    "check_v7_cumulative_spend",
    "check_v8_cadence_compliance",
    "verify_transaction",
]
