"""
Unit tests for Layer 2 — Behavioral Risk Detector.

Tests cover:
    - Sequence, timing, credential, and velocity feature extractors
    - BehavioralRiskModel training, prediction, scoring, and fail-closed handling
    - CostModel threshold optimization
"""

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from layer2_detector.cost_model import CostModel, CostOptimizationResult
from layer2_detector.features import (
    FEATURE_COLUMNS,
    _extract_credential_features,
    _extract_sequence_features,
    _extract_timing_features,
    _extract_velocity_features,
    extract_all_features,
)
from layer2_detector.model import BehavioralRiskModel, BehavioralRiskResult


# ── Feature Extractor Fixtures ───────────────────────────────────────

@pytest.fixture
def canonical_events():
    return [
        {"step_index": 0, "step_name": "discover_mandate", "status": "success", "latency_ms": 150.0, "is_retry": False},
        {"step_index": 1, "step_name": "validate_mandate", "status": "success", "latency_ms": 200.0, "is_retry": False},
        {"step_index": 2, "step_name": "build_cart", "status": "success", "latency_ms": 300.0, "is_retry": False},
        {"step_index": 3, "step_name": "compute_amount", "status": "success", "latency_ms": 120.0, "is_retry": False},
        {"step_index": 4, "step_name": "check_consent", "status": "success", "latency_ms": 180.0, "consent_token": "TOK123456", "is_retry": False},
        {"step_index": 5, "step_name": "initiate_payment", "status": "success", "latency_ms": 400.0, "is_retry": False},
        {"step_index": 6, "step_name": "confirm_payment", "status": "success", "latency_ms": 250.0, "is_retry": False},
    ]


@pytest.fixture
def skip_ahead_events():
    """Payment without preceding cart or consent."""
    return [
        {"step_index": 0, "step_name": "discover_mandate", "status": "success", "latency_ms": 80.0, "is_retry": False},
        {"step_index": 1, "step_name": "validate_mandate", "status": "success", "latency_ms": 80.0, "is_retry": False},
        {"step_index": 2, "step_name": "initiate_payment", "status": "success", "latency_ms": 80.0, "is_retry": False},
        {"step_index": 3, "step_name": "confirm_payment", "status": "success", "latency_ms": 80.0, "is_retry": False},
    ]


# ── Feature Extraction Tests ─────────────────────────────────────────

class TestSequenceFeatures:
    def test_canonical_sequence_features(self, canonical_events):
        feats = _extract_sequence_features(canonical_events)
        assert feats["seq_length"] == 7
        assert feats["seq_has_cart"] == 1
        assert feats["seq_has_consent"] == 1
        assert feats["seq_has_payment"] == 1
        assert feats["seq_skip_ahead"] == 0
        assert feats["seq_edit_distance"] == 0
        assert feats["seq_edit_distance_norm"] == 0.0
        assert feats["seq_bigram_novelty"] == 0.0

    def test_skip_ahead_detection(self, skip_ahead_events):
        feats = _extract_sequence_features(skip_ahead_events)
        assert feats["seq_skip_ahead"] == 1
        assert feats["seq_has_cart"] == 0
        assert feats["seq_has_consent"] == 0
        assert feats["seq_edit_distance"] > 0

    def test_empty_events_fail_closed(self):
        feats = _extract_sequence_features([])
        assert feats["seq_length"] == 0
        assert feats["seq_skip_ahead"] == 1
        assert feats["seq_edit_distance_norm"] == 1.0


class TestTimingFeatures:
    def test_normal_agent_timing(self, canonical_events):
        feats = _extract_timing_features(canonical_events)
        assert feats["timing_latency_mean_ms"] > 0
        assert feats["timing_latency_std_ms"] > 0
        assert feats["timing_session_duration_ms"] == 1600.0
        assert feats["timing_low_variance_flag"] == 0

    def test_scripted_bot_low_variance_flag(self):
        # Mechanically uniform 50ms intervals
        bot_events = [
            {"step_index": i, "step_name": "step", "latency_ms": 50.0 + np.random.normal(0, 1)}
            for i in range(5)
        ]
        feats = _extract_timing_features(bot_events)
        assert feats["timing_low_variance_flag"] == 1
        assert feats["timing_latency_cv"] < 0.15

    def test_empty_timing(self):
        feats = _extract_timing_features([])
        assert feats["timing_latency_mean_ms"] == 0.0
        assert feats["timing_session_duration_ms"] == 0.0


class TestCredentialFeatures:
    def test_fresh_consent_token(self, canonical_events):
        history = {}
        feats = _extract_credential_features(canonical_events, history, 100000000)
        assert feats["cred_has_consent_token"] == 1
        assert feats["cred_token_reused"] == 0
        assert feats["cred_token_reuse_count"] == 0

    def test_replayed_consent_token(self, canonical_events):
        token = "TOK123456"
        history = {
            token: [{"session_id": "SES_OLD", "timestamp_epoch_ms": 100000000 - 3600000 * 5}]
        }
        feats = _extract_credential_features(canonical_events, history, 100000000)
        assert feats["cred_has_consent_token"] == 1
        assert feats["cred_token_reused"] == 1
        assert feats["cred_token_reuse_count"] == 1
        assert feats["cred_token_age_hours"] == 5.0


class TestVelocityFeatures:
    def test_first_transaction_velocity(self):
        txn = {"amount": 1000.0, "timestamp_epoch_ms": 100000000, "cumulative_mandate_spend": 1000.0}
        mandate = {"amount_ceiling": 2000, "cadence": "weekly", "cumulative_spend_limit": 50000}
        feats = _extract_velocity_features(txn, [], mandate)
        assert feats["vel_is_first_txn"] == 1
        assert feats["vel_txns_last_1h"] == 0
        assert feats["vel_amount_z_score"] == 0.0

    def test_burst_and_z_score(self):
        mandate = {"amount_ceiling": 5000, "cadence": "weekly", "cumulative_spend_limit": 100000}
        now_ms = 100000000
        # Historical transactions of ~1000 INR with variance
        amounts = [950.0, 1050.0, 1000.0, 980.0, 1020.0]
        history = [
            {"amount": amounts[i - 1], "timestamp_epoch_ms": now_ms - (i * 60000)}
            for i in range(1, 6)
        ]
        # Current transaction is 5000 INR
        txn = {"amount": 5000.0, "timestamp_epoch_ms": now_ms, "cumulative_mandate_spend": 10000.0}
        feats = _extract_velocity_features(txn, history, mandate)
        assert feats["vel_txns_last_1h"] == 5
        assert feats["vel_burst_score"] > 0
        assert feats["vel_amount_z_score"] > 0  # 5000 is much higher than ~1000 baseline


# ── Model & Scoring Tests ────────────────────────────────────────────

class TestBehavioralRiskModel:
    @pytest.fixture
    def synthetic_data(self):
        np.random.seed(42)
        n = 200
        data = {col: np.random.randn(n) for col in FEATURE_COLUMNS}
        df = pd.DataFrame(data)
        y = (np.random.rand(n) > 0.85).astype(int)
        return df, y

    def test_fit_and_predict_proba(self, synthetic_data):
        X, y = synthetic_data
        model = BehavioralRiskModel(n_estimators=10, max_depth=3)
        model.fit(X, y)

        probs = model.predict_proba(X)
        assert len(probs) == len(X)
        assert np.all(probs >= 0.0)
        assert np.all(probs <= 1.0)

    def test_score_single_structured_output(self, synthetic_data):
        X, y = synthetic_data
        model = BehavioralRiskModel(n_estimators=10, max_depth=3)
        model.fit(X, y)

        sample_features = X.iloc[0].to_dict()
        result = model.score_single(sample_features, threshold=0.5)

        assert isinstance(result, BehavioralRiskResult)
        assert 0.0 <= result.risk_score <= 1.0
        assert result.verdict in ("pass", "suspicious", "attack")
        assert len(result.top_risk_factors) > 0
        assert "risk_score" in result.evidence

    def test_fail_closed_unfitted_model(self):
        model = BehavioralRiskModel()
        result = model.score_single({})
        assert result.verdict == "attack"
        assert result.risk_score == 1.0

    def test_save_and_load_persistence(self, synthetic_data):
        X, y = synthetic_data
        model = BehavioralRiskModel(n_estimators=10, max_depth=3)
        model.fit(X, y)

        with tempfile.TemporaryDirectory() as tmpdir:
            model_path = Path(tmpdir) / "test_model.joblib"
            model.save(model_path)
            assert model_path.exists()

            loaded = BehavioralRiskModel.load(model_path)
            assert loaded.is_fitted is True
            probs_orig = model.predict_proba(X)
            probs_loaded = loaded.predict_proba(X)
            np.testing.assert_allclose(probs_orig, probs_loaded)


# ── Cost Model Tests ─────────────────────────────────────────────────

class TestCostModel:
    def test_threshold_optimization(self):
        np.random.seed(42)
        y_true = np.array([0] * 950 + [1] * 50)
        # Probabilities correlated with ground truth
        y_prob = np.where(y_true == 1, np.random.uniform(0.3, 0.9, size=1000), np.random.uniform(0.01, 0.2, size=1000))

        cost_model = CostModel(cost_fp=50.0, cost_fn=2000.0)
        result = cost_model.optimize_threshold(y_true, y_prob)

        assert isinstance(result, CostOptimizationResult)
        assert 0.01 <= result.optimal_threshold <= 0.99
        assert result.min_cost <= result.cost_at_default_05
        assert len(result.threshold_curve) == 100
