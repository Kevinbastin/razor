"""Focused tests for Layer 3's explainable I1 and I4 rules."""

from layer3_intent.detector import IntentIntegrityDetector


def _detector():
    mandate = {"mandate_id": "M1", "purpose": "weekly grocery top-up"}
    txn = {"cart_items": ["rice", "dal"], "cart_category": "grocery_stores"}
    return IntentIntegrityDetector(i1_threshold=0.35, min_history=3).fit([mandate], [txn]), mandate


def test_i1_flags_outcome_that_diverges_from_purpose():
    detector, mandate = _detector()
    result = detector.score_transaction(
        {"cart_items": ["gold chain", "luxury watch"], "cart_category": "grocery_stores", "amount": 900, "timestamp": "2025-06-01T20:00:00+05:30", "beneficiary_id": "new"},
        mandate, [],
    )
    assert result.verdict == "flagged"
    assert "I1" in result.signals_triggered
    assert result.evidence["I1"]["similarity_score"] < result.evidence["I1"]["threshold"]


def test_i4_requires_the_full_escalation_triple():
    detector, mandate = _detector()
    history = [
        {"beneficiary_id": "old", "amount": amount, "timestamp": "2025-06-01T09:00:00+05:30"}
        for amount in (100, 110, 120, 130)
    ]
    result = detector.score_transaction(
        {"cart_items": ["rice"], "cart_category": "grocery_stores", "beneficiary_id": "new", "amount": 500, "timestamp": "2025-06-01T20:00:00+05:30"},
        mandate, history,
    )
    assert "I4" in result.signals_triggered
    # A high-value established beneficiary must not meet the triple.
    established = detector.score_transaction(
        {"cart_items": ["rice"], "cart_category": "grocery_stores", "beneficiary_id": "old", "amount": 500, "timestamp": "2025-06-01T20:00:00+05:30"},
        mandate, history,
    )
    assert "I4" not in established.signals_triggered


def test_i2_flags_new_low_reputation_domain_immediately_before_payment():
    detector, mandate = _detector()
    result = detector.score_transaction(
        {"cart_items": ["rice"], "cart_category": "grocery_stores", "beneficiary_id": "old", "amount": 100,
         "timestamp": "2025-06-01T10:00:00+05:30",
         "source_exposures": [{"url": "https://poisoned-search.example/deal", "timestamp": "2025-06-01T09:59:30+05:30", "reputation": 0.1}]},
        mandate, [],
    )
    assert "I2" in result.signals_triggered
    assert result.evidence["I2"]["immediate_exposures"][0]["low_reputation"] is True
