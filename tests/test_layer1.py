"""
Unit tests for Layer 1 — Mandate Verifier.

Tests each check individually:
  - One passing case
  - One failing case
  - One malformed-input case (confirms fail-closed behavior)

Plus integration tests for verify_transaction.
"""

import pytest
from layer1_verifier.checks import (
    CheckResult,
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


# ── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def valid_mandate():
    """A standard active mandate for testing."""
    return {
        "mandate_id": "UMN135EA7E049064089",
        "payer_vpa": "rahul123@okicici",
        "payee_vpa": "zepto.merchant@razorpay",
        "amount_ceiling": 5000,
        "amount_rule": "max",
        "permitted_mccs": ["5411", "5812"],
        "permitted_categories": ["grocery_stores", "restaurants_food_delivery"],
        "time_window_start_hour": 6,
        "time_window_end_hour": 23,
        "cadence": "weekly",
        "purpose": "weekly grocery top-up, ~₹5000",
        "lifecycle_state": "active",
        "granted_at": "2025-03-01T10:00:00+05:30",
        "expires_at": "2025-09-01T10:00:00+05:30",
        "beneficiaries": ["BEN_AAAAAAAA", "BEN_BBBBBBBB"],
        "primary_agent_type": "standard_agent",
        "cumulative_spend_limit": 100000,
    }


@pytest.fixture
def valid_transaction():
    """A standard legitimate transaction for testing."""
    return {
        "transaction_id": "TXN_TEST12345678",
        "mandate_id": "UMN135EA7E049064089",
        "timestamp": "2025-06-15T14:30:00+05:30",
        "amount": 1500.0,
        "cart_mcc": "5411",
        "cart_category": "grocery_stores",
        "cart_items": ["rice", "dal", "oil"],
        "agent_type": "standard_agent",
        "cumulative_mandate_spend": 25000.0,
    }


# ── V1: Amount Ceiling ───────────────────────────────────────────────

class TestV1AmountCeiling:
    def test_pass_under_ceiling(self, valid_transaction, valid_mandate):
        result = check_v1_amount_ceiling(valid_transaction, valid_mandate)
        assert result.passed is True
        assert result.check_id == "V1"
        assert result.reason == "amount_within_ceiling"

    def test_pass_exact_at_ceiling(self, valid_transaction, valid_mandate):
        valid_transaction["amount"] = 5000.0
        result = check_v1_amount_ceiling(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_fail_over_ceiling(self, valid_transaction, valid_mandate):
        valid_transaction["amount"] = 7500.0
        result = check_v1_amount_ceiling(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "amount_exceeds_ceiling"
        assert result.evidence["exceeded_by"] == 2500.0

    def test_fail_exact_rule_mismatch(self, valid_transaction, valid_mandate):
        valid_mandate["amount_rule"] = "exact"
        valid_transaction["amount"] = 3000.0
        result = check_v1_amount_ceiling(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "amount_deviates_from_exact"

    def test_pass_exact_rule_match(self, valid_transaction, valid_mandate):
        valid_mandate["amount_rule"] = "exact"
        valid_transaction["amount"] = 5000.0
        result = check_v1_amount_ceiling(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_malformed_missing_amount(self, valid_mandate):
        txn = {"mandate_id": "UMN135EA7E049064089"}
        result = check_v1_amount_ceiling(txn, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_malformed_non_numeric_amount(self, valid_mandate):
        txn = {"amount": "not_a_number", "mandate_id": "X"}
        result = check_v1_amount_ceiling(txn, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_malformed_negative_amount(self, valid_mandate):
        txn = {"amount": -100, "mandate_id": "X"}
        result = check_v1_amount_ceiling(txn, valid_mandate)
        assert result.passed is False
        assert "negative" in result.reason


# ── V2: Category Scope ──────────────────────────────────────────────

class TestV2CategoryScope:
    def test_pass_mcc_in_scope(self, valid_transaction, valid_mandate):
        result = check_v2_category_scope(valid_transaction, valid_mandate)
        assert result.passed is True
        assert result.reason == "category_in_scope"

    def test_fail_mcc_out_of_scope(self, valid_transaction, valid_mandate):
        valid_transaction["cart_mcc"] = "5944"  # jewelry
        valid_transaction["cart_category"] = "jewelry_stores"
        result = check_v2_category_scope(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "category_out_of_scope"

    def test_pass_category_name_match(self, valid_transaction, valid_mandate):
        valid_transaction["cart_mcc"] = "9999"  # unknown MCC
        valid_transaction["cart_category"] = "grocery_stores"
        result = check_v2_category_scope(valid_transaction, valid_mandate)
        assert result.passed is True  # category name fallback

    def test_malformed_no_cart_info(self, valid_mandate):
        txn = {"mandate_id": "X"}
        result = check_v2_category_scope(txn, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_malformed_no_permitted(self, valid_transaction):
        mandate = {"mandate_id": "X"}
        result = check_v2_category_scope(valid_transaction, mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason


# ── V3: Time Window ─────────────────────────────────────────────────

class TestV3TimeWindow:
    def test_pass_within_window(self, valid_transaction, valid_mandate):
        result = check_v3_time_window(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_fail_outside_window(self, valid_transaction, valid_mandate):
        valid_transaction["timestamp"] = "2025-06-15T03:00:00+05:30"  # 3 AM
        result = check_v3_time_window(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "outside_time_window"

    def test_pass_any_time_window(self, valid_transaction, valid_mandate):
        valid_mandate["time_window_start_hour"] = 0
        valid_mandate["time_window_end_hour"] = 24
        valid_transaction["timestamp"] = "2025-06-15T03:00:00+05:30"
        result = check_v3_time_window(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_malformed_missing_timestamp(self, valid_mandate):
        txn = {"mandate_id": "X"}
        result = check_v3_time_window(txn, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_malformed_invalid_timestamp(self, valid_mandate):
        txn = {"timestamp": "not-a-date", "mandate_id": "X"}
        result = check_v3_time_window(txn, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason


# ── V4: Lifecycle State ─────────────────────────────────────────────

class TestV4LifecycleState:
    def test_pass_active(self, valid_transaction, valid_mandate):
        result = check_v4_lifecycle_state(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_fail_paused(self, valid_transaction, valid_mandate):
        valid_mandate["lifecycle_state"] = "paused"
        result = check_v4_lifecycle_state(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "mandate_paused"

    def test_fail_revoked(self, valid_transaction, valid_mandate):
        valid_mandate["lifecycle_state"] = "revoked"
        result = check_v4_lifecycle_state(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "mandate_revoked"

    def test_fail_expired(self, valid_transaction, valid_mandate):
        valid_mandate["lifecycle_state"] = "expired"
        result = check_v4_lifecycle_state(valid_transaction, valid_mandate)
        assert result.passed is False

    def test_malformed_missing_state(self, valid_transaction):
        result = check_v4_lifecycle_state(valid_transaction, {})
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_malformed_invalid_state(self, valid_transaction):
        result = check_v4_lifecycle_state(valid_transaction, {"lifecycle_state": "banana"})
        assert result.passed is False
        assert "malformed_input" in result.reason


# ── V5: UMN Integrity ───────────────────────────────────────────────

class TestV5UmnIntegrity:
    def test_pass_valid_umn(self, valid_transaction, valid_mandate):
        result = check_v5_umn_integrity(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_pass_in_registry(self, valid_transaction, valid_mandate):
        known = {"UMN135EA7E049064089"}
        result = check_v5_umn_integrity(valid_transaction, valid_mandate, known)
        assert result.passed is True

    def test_fail_not_in_registry(self, valid_transaction, valid_mandate):
        known = {"UMN000000000000000"}
        result = check_v5_umn_integrity(valid_transaction, valid_mandate, known)
        assert result.passed is False
        assert result.reason == "umn_not_in_registry"

    def test_fail_malformed_umn(self, valid_mandate):
        txn = {"mandate_id": "BADFORMAT123"}
        result = check_v5_umn_integrity(txn, valid_mandate)
        assert result.passed is False
        assert result.reason == "umn_malformed"

    def test_fail_missing_mandate_id(self, valid_mandate):
        result = check_v5_umn_integrity({}, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_fail_mismatch(self, valid_mandate):
        txn = {"mandate_id": "UMNAAAAAAAAAAAAAAAA"}
        result = check_v5_umn_integrity(txn, valid_mandate)
        assert result.passed is False
        assert result.reason == "umn_mismatch_with_mandate"


# ── V6: Key Attestation ─────────────────────────────────────────────

class TestV6KeyAttestation:
    def test_pass_matching_agent(self, valid_transaction, valid_mandate):
        result = check_v6_key_attestation(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_fail_mismatched_agent(self, valid_transaction, valid_mandate):
        valid_transaction["agent_type"] = "fast_bot"
        result = check_v6_key_attestation(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "agent_identity_mismatch"

    def test_fail_spoofed_type(self, valid_transaction, valid_mandate):
        valid_transaction["agent_type"] = "unknown_bot"
        result = check_v6_key_attestation(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.reason == "spoofed_agent_type_detected"

    def test_pass_with_registry(self, valid_transaction, valid_mandate):
        registry = {"UMN135EA7E049064089": "standard_agent"}
        result = check_v6_key_attestation(valid_transaction, valid_mandate, registry)
        assert result.passed is True

    def test_fail_with_registry_mismatch(self, valid_transaction, valid_mandate):
        registry = {"UMN135EA7E049064089": "cautious_agent"}
        result = check_v6_key_attestation(valid_transaction, valid_mandate, registry)
        assert result.passed is False

    def test_malformed_missing_agent(self, valid_mandate):
        result = check_v6_key_attestation({}, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason


# ── V7: Cumulative Spend ────────────────────────────────────────────

class TestV7CumulativeSpend:
    def test_pass_under_limit(self, valid_transaction, valid_mandate):
        result = check_v7_cumulative_spend(valid_transaction, valid_mandate)
        assert result.passed is True
        assert result.evidence["utilization_pct"] == 25.0

    def test_fail_over_limit(self, valid_transaction, valid_mandate):
        valid_transaction["cumulative_mandate_spend"] = 150000.0
        result = check_v7_cumulative_spend(valid_transaction, valid_mandate)
        assert result.passed is False
        assert result.evidence["exceeded_by"] == 50000.0

    def test_pass_at_exactly_limit(self, valid_transaction, valid_mandate):
        valid_transaction["cumulative_mandate_spend"] = 100000.0
        result = check_v7_cumulative_spend(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_malformed_missing_spend(self, valid_mandate):
        result = check_v7_cumulative_spend({}, valid_mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_malformed_zero_limit(self, valid_transaction):
        mandate = {"cumulative_spend_limit": 0}
        result = check_v7_cumulative_spend(valid_transaction, mandate)
        assert result.passed is False
        assert "malformed_input" in result.reason


# ── V8: Cadence Compliance ──────────────────────────────────────────

class TestV8CadenceCompliance:
    def test_pass_no_history(self, valid_transaction, valid_mandate):
        result = check_v8_cadence_compliance(valid_transaction, valid_mandate)
        assert result.passed is True

    def test_pass_on_demand(self, valid_transaction, valid_mandate):
        valid_mandate["cadence"] = "on_demand"
        result = check_v8_cadence_compliance(
            valid_transaction, valid_mandate,
            recent_txn_timestamps=["2025-06-15T14:00:00+05:30"],
        )
        assert result.passed is True

    def test_pass_weekly_adequate_gap(self, valid_transaction, valid_mandate):
        # 7 days before
        result = check_v8_cadence_compliance(
            valid_transaction, valid_mandate,
            recent_txn_timestamps=["2025-06-08T14:00:00+05:30"],
        )
        assert result.passed is True

    def test_fail_weekly_too_frequent(self, valid_transaction, valid_mandate):
        # 5 minutes prior — violates weekly minimum interval (15 min)
        result = check_v8_cadence_compliance(
            valid_transaction, valid_mandate,
            recent_txn_timestamps=["2025-06-15T14:25:00+05:30"],
        )
        assert result.passed is False
        assert result.reason == "cadence_violation_too_frequent"

    def test_malformed_missing_cadence(self, valid_transaction):
        result = check_v8_cadence_compliance(valid_transaction, {})
        assert result.passed is False
        assert "malformed_input" in result.reason

    def test_malformed_invalid_cadence(self, valid_transaction):
        result = check_v8_cadence_compliance(
            valid_transaction, {"cadence": "hourly"}
        )
        assert result.passed is False
        assert "malformed_input" in result.reason


# ── Integration: verify_transaction ──────────────────────────────────

class TestVerifyTransaction:
    def test_all_pass(self, valid_transaction, valid_mandate):
        result = verify_transaction(valid_transaction, valid_mandate)
        assert result.verdict == "pass"
        assert result.failed_checks == []
        assert "V1" in result.evidence
        assert result.mandate_snapshot["mandate_id"] == "UMN135EA7E049064089"

    def test_multiple_failures(self, valid_transaction, valid_mandate):
        valid_transaction["amount"] = 99999.0  # V1 fail
        valid_mandate["lifecycle_state"] = "revoked"  # V4 fail
        result = verify_transaction(valid_transaction, valid_mandate)
        assert result.verdict == "fail"
        assert "V1" in result.failed_checks
        assert "V4" in result.failed_checks

    def test_fail_closed_none_transaction(self, valid_mandate):
        result = verify_transaction(None, valid_mandate)
        assert result.verdict == "fail"
        assert "INPUT" in result.failed_checks

    def test_fail_closed_empty_transaction(self, valid_mandate):
        result = verify_transaction({}, valid_mandate)
        # Empty dict triggers fail-closed in individual checks
        assert result.verdict == "fail"

    def test_fail_closed_none_mandate(self, valid_transaction):
        result = verify_transaction(valid_transaction, None)
        assert result.verdict == "fail"

    def test_output_structure(self, valid_transaction, valid_mandate):
        result = verify_transaction(valid_transaction, valid_mandate)
        d = result.to_dict()
        assert "verdict" in d
        assert "failed_checks" in d
        assert "evidence" in d
        assert "mandate_snapshot" in d
        assert isinstance(d["failed_checks"], list)
        assert isinstance(d["evidence"], dict)
