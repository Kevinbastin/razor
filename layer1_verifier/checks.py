"""
Layer 1 — Individual verification checks.

Each check is a pure function:
    Input:  transaction dict, mandate dict, optional context
    Output: CheckResult with pass/fail, check_id, and structured evidence

Every check fails closed on malformed/missing input.
"""

import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import structlog
from observability.metrics import METRICS

logger = structlog.get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# UMN format: "UMN" followed by exactly 16 hex characters (upper case)
UMN_PATTERN = re.compile(r"^UMN[0-9A-F]{16}$")

VALID_LIFECYCLE_STATES = {"active", "paused", "revoked", "expired"}
VALID_CADENCES = {"daily", "weekly", "biweekly", "monthly", "on_demand"}


# ── Data structures ──────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Result of a single verification check."""

    check_id: str
    passed: bool
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerificationResult:
    """
    Complete verification output — consumed by Layer 4.

    Exact structure:
    {
        "verdict": "pass" | "fail",
        "failed_checks": ["V1", "V4", ...],
        "evidence": { "V1": { ... }, "V2": { ... }, ... },
        "mandate_snapshot": { umn, state, ceiling, ... }
    }
    """

    verdict: str  # "pass" or "fail"
    failed_checks: List[str]
    evidence: Dict[str, Dict[str, Any]]
    mandate_snapshot: Dict[str, Any]

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "failed_checks": self.failed_checks,
            "evidence": self.evidence,
            "mandate_snapshot": self.mandate_snapshot,
        }


# ── Helpers ──────────────────────────────────────────────────────────

def _safe_float(value: Any, field_name: str) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any, field_name: str) -> Optional[int]:
    """Safely convert to int, returning None on failure."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_json_list(value: Any) -> Optional[list]:
    """Parse a JSON string list or return list as-is."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return None


def _fail_malformed(check_id: str, reason: str) -> CheckResult:
    """Convenience: create a fail result for malformed input."""
    return CheckResult(
        check_id=check_id,
        passed=False,
        reason=f"malformed_input: {reason}",
        evidence={"error": reason, "fail_mode": "malformed_input"},
    )


# ── V1: Amount Ceiling ───────────────────────────────────────────────

def check_v1_amount_ceiling(
    transaction: dict,
    mandate: dict,
) -> CheckResult:
    """
    V1: Check transaction amount against mandate ceiling.

    Rules:
    - amount_rule == "max": amount must be <= ceiling
    - amount_rule == "exact": amount must equal ceiling exactly
    - Fail closed on missing/malformed amount or ceiling.
    """
    check_id = "V1"

    # Extract and validate
    amount = _safe_float(transaction.get("amount"), "amount")
    ceiling = _safe_float(mandate.get("amount_ceiling"), "amount_ceiling")
    amount_rule = mandate.get("amount_rule")

    if amount is None:
        return _fail_malformed(check_id, "transaction.amount missing or non-numeric")
    if ceiling is None:
        return _fail_malformed(check_id, "mandate.amount_ceiling missing or non-numeric")
    if amount_rule not in ("max", "exact"):
        return _fail_malformed(check_id, f"mandate.amount_rule invalid: {amount_rule!r}")
    if amount < 0:
        return _fail_malformed(check_id, f"transaction.amount is negative: {amount}")

    evidence = {
        "transaction_amount": amount,
        "mandate_ceiling": ceiling,
        "amount_rule": amount_rule,
    }

    if amount_rule == "max":
        passed = amount <= ceiling
        if not passed:
            evidence["exceeded_by"] = round(amount - ceiling, 2)
            evidence["exceeded_pct"] = round((amount - ceiling) / ceiling * 100, 2)
        return CheckResult(
            check_id=check_id,
            passed=passed,
            reason="amount_within_ceiling" if passed else "amount_exceeds_ceiling",
            evidence=evidence,
        )
    else:  # exact
        # Allow tiny floating point tolerance
        passed = abs(amount - ceiling) < 0.01
        if not passed:
            evidence["deviation"] = round(amount - ceiling, 2)
        return CheckResult(
            check_id=check_id,
            passed=passed,
            reason="amount_matches_exact" if passed else "amount_deviates_from_exact",
            evidence=evidence,
        )


# ── V2: Category Scope ──────────────────────────────────────────────

def check_v2_category_scope(
    transaction: dict,
    mandate: dict,
) -> CheckResult:
    """
    V2: Check that the transaction's MCC is within the mandate's permitted set.

    Checks both MCC code and category name for flexibility.
    """
    check_id = "V2"

    txn_mcc = transaction.get("cart_mcc")
    txn_category = transaction.get("cart_category")
    permitted_mccs = _parse_json_list(mandate.get("permitted_mccs"))
    permitted_categories = _parse_json_list(mandate.get("permitted_categories"))

    if txn_mcc is None and txn_category is None:
        return _fail_malformed(check_id, "transaction has no cart_mcc or cart_category")
    if permitted_mccs is None and permitted_categories is None:
        return _fail_malformed(check_id, "mandate has no permitted_mccs or permitted_categories")

    evidence = {
        "transaction_mcc": txn_mcc,
        "transaction_category": txn_category,
        "permitted_mccs": permitted_mccs,
        "permitted_categories": permitted_categories,
    }

    # Check MCC match first, then category name
    mcc_match = txn_mcc in (permitted_mccs or []) if txn_mcc else False
    cat_match = txn_category in (permitted_categories or []) if txn_category else False

    passed = mcc_match or cat_match

    return CheckResult(
        check_id=check_id,
        passed=passed,
        reason="category_in_scope" if passed else "category_out_of_scope",
        evidence=evidence,
    )


# ── V3: Time Window ─────────────────────────────────────────────────

def check_v3_time_window(
    transaction: dict,
    mandate: dict,
) -> CheckResult:
    """
    V3: Check that the transaction timestamp falls within the mandate's
    approved time window (hour-of-day check).

    Handles wrap-around windows (e.g., 22:00 - 06:00).
    """
    check_id = "V3"

    timestamp_str = transaction.get("timestamp")
    tw_start = _safe_int(mandate.get("time_window_start_hour"), "time_window_start_hour")
    tw_end = _safe_int(mandate.get("time_window_end_hour"), "time_window_end_hour")

    if timestamp_str is None:
        return _fail_malformed(check_id, "transaction.timestamp missing")
    if tw_start is None or tw_end is None:
        return _fail_malformed(check_id, "mandate time_window_start/end_hour missing")
    if not (0 <= tw_start <= 23):
        return _fail_malformed(check_id, f"time_window_start_hour out of range: {tw_start}")
    if not (0 <= tw_end <= 24):
        return _fail_malformed(check_id, f"time_window_end_hour out of range: {tw_end}")

    try:
        txn_time = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        return _fail_malformed(check_id, f"transaction.timestamp not valid ISO: {timestamp_str!r}")

    txn_hour = txn_time.hour

    evidence = {
        "transaction_hour": txn_hour,
        "window_start_hour": tw_start,
        "window_end_hour": tw_end,
        "transaction_timestamp": timestamp_str,
    }

    # Handle normal and wrap-around windows
    if tw_start == 0 and tw_end == 24:
        # Any time is fine
        passed = True
    elif tw_end > tw_start:
        # Normal window: e.g. 8-22
        passed = tw_start <= txn_hour < tw_end
    elif tw_end < tw_start:
        # Wrap-around: e.g. 22-6 means 22,23,0,1,2,3,4,5
        passed = txn_hour >= tw_start or txn_hour < tw_end
    else:
        # tw_start == tw_end and not 0==0... means zero-width window
        passed = False

    return CheckResult(
        check_id=check_id,
        passed=passed,
        reason="within_time_window" if passed else "outside_time_window",
        evidence=evidence,
    )


# ── V4: Lifecycle State ─────────────────────────────────────────────

def check_v4_lifecycle_state(
    transaction: dict,
    mandate: dict,
) -> CheckResult:
    """
    V4: Reject transactions against mandates that are not in "active" state.

    paused → fail (mandate temporarily suspended)
    revoked → fail (mandate permanently cancelled)
    expired → fail (mandate past its validity period)
    active → pass
    """
    check_id = "V4"

    state = mandate.get("lifecycle_state")

    if state is None:
        return _fail_malformed(check_id, "mandate.lifecycle_state missing")
    if state not in VALID_LIFECYCLE_STATES:
        return _fail_malformed(check_id, f"mandate.lifecycle_state invalid: {state!r}")

    evidence = {
        "lifecycle_state": state,
    }

    passed = state == "active"

    if not passed:
        evidence["rejection_reason"] = {
            "paused": "mandate_temporarily_suspended",
            "revoked": "mandate_permanently_cancelled",
            "expired": "mandate_past_validity",
        }.get(state, f"unrecognized_state_{state}")

    return CheckResult(
        check_id=check_id,
        passed=passed,
        reason="mandate_active" if passed else f"mandate_{state}",
        evidence=evidence,
    )


# ── V5: UMN Integrity ───────────────────────────────────────────────

def check_v5_umn_integrity(
    transaction: dict,
    mandate: dict,
    known_mandate_ids: Optional[Set[str]] = None,
) -> CheckResult:
    """
    V5: Validate that the UMN is well-formed and matches a known mandate.

    Format: "UMN" + 16 uppercase hex chars (e.g. UMN135EA7E049064089)
    """
    check_id = "V5"

    txn_mandate_id = transaction.get("mandate_id")
    mandate_id = mandate.get("mandate_id")

    if txn_mandate_id is None:
        return _fail_malformed(check_id, "transaction.mandate_id missing")
    if not isinstance(txn_mandate_id, str):
        return _fail_malformed(check_id, f"transaction.mandate_id not a string: {type(txn_mandate_id)}")

    evidence = {
        "transaction_mandate_id": txn_mandate_id,
        "mandate_mandate_id": mandate_id,
    }

    # Check format
    if not UMN_PATTERN.match(txn_mandate_id):
        evidence["format_valid"] = False
        return CheckResult(
            check_id=check_id,
            passed=False,
            reason="umn_malformed",
            evidence=evidence,
        )

    evidence["format_valid"] = True

    # Check ID match between transaction and mandate
    if mandate_id is not None and txn_mandate_id != mandate_id:
        evidence["ids_match"] = False
        return CheckResult(
            check_id=check_id,
            passed=False,
            reason="umn_mismatch_with_mandate",
            evidence=evidence,
        )

    evidence["ids_match"] = True

    # Check against known mandate registry if provided
    if known_mandate_ids is not None:
        in_registry = txn_mandate_id in known_mandate_ids
        evidence["in_registry"] = in_registry
        if not in_registry:
            return CheckResult(
                check_id=check_id,
                passed=False,
                reason="umn_not_in_registry",
                evidence=evidence,
            )

    return CheckResult(
        check_id=check_id,
        passed=True,
        reason="umn_valid",
        evidence=evidence,
    )


# ── V6: Key Attestation ─────────────────────────────────────────────

def check_v6_key_attestation(
    transaction: dict,
    mandate: dict,
    key_registry: Optional[Dict[str, str]] = None,
) -> CheckResult:
    """
    V6: Verify the transaction's agent type matches the agent registered
    for this mandate (simulated key/identity attestation).

    In production this would check signing keys; here we simulate it
    by comparing agent_type against primary_agent_type.
    """
    check_id = "V6"

    txn_agent = transaction.get("agent_type")
    mandate_agent = mandate.get("primary_agent_type")
    mandate_id = transaction.get("mandate_id", mandate.get("mandate_id"))

    if txn_agent is None:
        return _fail_malformed(check_id, "transaction.agent_type missing")
    if mandate_agent is None and key_registry is None:
        return _fail_malformed(check_id, "no agent identity reference available")

    evidence = {
        "transaction_agent_type": txn_agent,
        "mandate_primary_agent_type": mandate_agent,
    }

    # Check against key registry first (if provided)
    if key_registry is not None and mandate_id is not None:
        registered_agent = key_registry.get(mandate_id)
        evidence["registry_agent_type"] = registered_agent
        if registered_agent is not None:
            passed = txn_agent == registered_agent
            return CheckResult(
                check_id=check_id,
                passed=passed,
                reason="key_attestation_pass" if passed else "agent_identity_mismatch",
                evidence=evidence,
            )

    # Fallback: compare against mandate's primary_agent_type
    if mandate_agent is not None:
        # Known spoofed types always fail
        spoofed_types = {"unknown_bot", "cloned_agent", "mitm_proxy"}
        if txn_agent in spoofed_types:
            evidence["is_known_spoofed_type"] = True
            return CheckResult(
                check_id=check_id,
                passed=False,
                reason="spoofed_agent_type_detected",
                evidence=evidence,
            )

        passed = txn_agent == mandate_agent
        return CheckResult(
            check_id=check_id,
            passed=passed,
            reason="key_attestation_pass" if passed else "agent_identity_mismatch",
            evidence=evidence,
        )

    # No reference available — fail closed
    return CheckResult(
        check_id=check_id,
        passed=False,
        reason="no_agent_identity_reference",
        evidence=evidence,
    )


# ── V7: Cumulative Spend ────────────────────────────────────────────

def check_v7_cumulative_spend(
    transaction: dict,
    mandate: dict,
) -> CheckResult:
    """
    V7: Check running cumulative spend against the mandate's budget.

    cumulative_mandate_spend includes the current transaction.
    """
    check_id = "V7"

    cumulative = _safe_float(
        transaction.get("cumulative_mandate_spend"),
        "cumulative_mandate_spend",
    )
    limit = _safe_float(
        mandate.get("cumulative_spend_limit"),
        "cumulative_spend_limit",
    )

    if cumulative is None:
        return _fail_malformed(check_id, "transaction.cumulative_mandate_spend missing")
    if limit is None:
        return _fail_malformed(check_id, "mandate.cumulative_spend_limit missing")
    if limit <= 0:
        return _fail_malformed(check_id, f"mandate.cumulative_spend_limit non-positive: {limit}")

    evidence = {
        "cumulative_spend": cumulative,
        "spend_limit": limit,
        "utilization_pct": round(cumulative / limit * 100, 2),
    }

    passed = cumulative <= limit

    if not passed:
        evidence["exceeded_by"] = round(cumulative - limit, 2)

    return CheckResult(
        check_id=check_id,
        passed=passed,
        reason="within_spend_limit" if passed else "cumulative_spend_exceeded",
        evidence=evidence,
    )


# ── V8: Cadence Compliance ──────────────────────────────────────────

def check_v8_cadence_compliance(
    transaction: dict,
    mandate: dict,
    recent_txn_timestamps: Optional[List[str]] = None,
) -> CheckResult:
    """
    V8: Check transaction frequency against the mandate's cadence rule.

    Cadence rules and minimum suspicious intervals:
        daily:     >= 1 hour between transactions
        weekly:    >= 12 hours between transactions
        biweekly:  >= 2 days between transactions
        monthly:   >= 5 days between transactions
        on_demand: no frequency restriction

    These are minimum gaps below which the frequency is suspicious.
    The cadence is a guideline, not a hard lockout — legitimate users
    may occasionally transact faster than the cadence suggests.

    If recent_txn_timestamps is not provided, this check passes
    (insufficient data to evaluate — we don't fail-close here because
    the first transaction for any mandate has no history).
    """
    check_id = "V8"

    cadence = mandate.get("cadence")
    timestamp_str = transaction.get("timestamp")

    if cadence is None:
        return _fail_malformed(check_id, "mandate.cadence missing")
    if cadence not in VALID_CADENCES:
        return _fail_malformed(check_id, f"mandate.cadence invalid: {cadence!r}")
    if timestamp_str is None:
        return _fail_malformed(check_id, "transaction.timestamp missing")

    try:
        txn_time = datetime.fromisoformat(timestamp_str)
    except (ValueError, TypeError):
        return _fail_malformed(check_id, f"transaction.timestamp invalid: {timestamp_str!r}")

    evidence = {
        "cadence": cadence,
        "transaction_timestamp": timestamp_str,
    }

    # on_demand has no frequency restriction
    if cadence == "on_demand":
        evidence["restriction"] = "none"
        return CheckResult(
            check_id=check_id,
            passed=True,
            reason="cadence_on_demand_no_restriction",
            evidence=evidence,
        )

    # If no recent history, pass (first transaction)
    if not recent_txn_timestamps:
        evidence["recent_history"] = "none_provided"
        return CheckResult(
            check_id=check_id,
            passed=True,
            reason="cadence_no_history_available",
            evidence=evidence,
        )

    # Minimum suspicious intervals — below these rapid gaps, flag as too frequent
    min_intervals = {
        "daily": timedelta(minutes=5),
        "weekly": timedelta(minutes=15),
        "biweekly": timedelta(minutes=30),
        "monthly": timedelta(hours=1),
    }
    min_interval = min_intervals[cadence]
    evidence["min_interval_minutes"] = min_interval.total_seconds() / 60

    # Find the most recent prior transaction
    most_recent = None
    for ts in recent_txn_timestamps:
        try:
            t = datetime.fromisoformat(ts)
            if t < txn_time and (most_recent is None or t > most_recent):
                most_recent = t
        except (ValueError, TypeError):
            continue

    if most_recent is None:
        evidence["most_recent_prior"] = None
        return CheckResult(
            check_id=check_id,
            passed=True,
            reason="cadence_no_prior_transaction",
            evidence=evidence,
        )

    gap = txn_time - most_recent
    evidence["most_recent_prior"] = most_recent.isoformat()
    evidence["gap_hours"] = round(gap.total_seconds() / 3600, 2)

    passed = gap >= min_interval

    return CheckResult(
        check_id=check_id,
        passed=passed,
        reason="cadence_compliant" if passed else "cadence_violation_too_frequent",
        evidence=evidence,
    )


# ── Top-level orchestrator ───────────────────────────────────────────

def verify_transaction(
    transaction: dict,
    mandate: dict,
    known_mandate_ids: Optional[Set[str]] = None,
    key_registry: Optional[Dict[str, str]] = None,
    recent_txn_timestamps: Optional[List[str]] = None,
) -> VerificationResult:
    """
    Run all 8 checks against a transaction and produce the Layer 1 verdict.

    Returns the exact structure Layer 4 expects:
    {
        "verdict": "pass" | "fail",
        "failed_checks": [...],
        "evidence": { "V1": {...}, "V2": {...}, ... },
        "mandate_snapshot": { ... }
    }

    Fail-closed: if transaction or mandate is None/empty, returns "fail".
    """
    # Fail closed on empty inputs
    if not transaction or not isinstance(transaction, dict):
        return VerificationResult(
            verdict="fail",
            failed_checks=["INPUT"],
            evidence={"INPUT": {"error": "transaction is None or not a dict"}},
            mandate_snapshot={},
        )
    if not mandate or not isinstance(mandate, dict):
        return VerificationResult(
            verdict="fail",
            failed_checks=["INPUT"],
            evidence={"INPUT": {"error": "mandate is None or not a dict"}},
            mandate_snapshot={},
        )

    # Run all checks
    results: List[CheckResult] = [
        check_v1_amount_ceiling(transaction, mandate),
        check_v2_category_scope(transaction, mandate),
        check_v3_time_window(transaction, mandate),
        check_v4_lifecycle_state(transaction, mandate),
        check_v5_umn_integrity(transaction, mandate, known_mandate_ids),
        check_v6_key_attestation(transaction, mandate, key_registry),
        check_v7_cumulative_spend(transaction, mandate),
        check_v8_cadence_compliance(transaction, mandate, recent_txn_timestamps),
    ]

    # Aggregate
    failed_checks = [r.check_id for r in results if not r.passed]
    evidence = {r.check_id: r.evidence for r in results}
    verdict = "fail" if failed_checks else "pass"

    # Build mandate snapshot
    mandate_snapshot = {
        "mandate_id": mandate.get("mandate_id"),
        "lifecycle_state": mandate.get("lifecycle_state"),
        "amount_ceiling": mandate.get("amount_ceiling"),
        "amount_rule": mandate.get("amount_rule"),
        "cadence": mandate.get("cadence"),
        "time_window_start_hour": mandate.get("time_window_start_hour"),
        "time_window_end_hour": mandate.get("time_window_end_hour"),
        "granted_at": mandate.get("granted_at"),
        "expires_at": mandate.get("expires_at"),
        "cumulative_spend_limit": mandate.get("cumulative_spend_limit"),
        "primary_agent_type": mandate.get("primary_agent_type"),
    }

    output = VerificationResult(
        verdict=verdict,
        failed_checks=failed_checks,
        evidence=evidence,
        mandate_snapshot=mandate_snapshot,
    )
    logger.info("layer1_decision", transaction_id=transaction.get("transaction_id"), mandate_id=transaction.get("mandate_id"), verdict=output.verdict, failed_checks=output.failed_checks)
    METRICS.record("layer1", output.verdict, actual_legitimate=transaction.get("label") == "legitimate" if "label" in transaction else None)
    return output
