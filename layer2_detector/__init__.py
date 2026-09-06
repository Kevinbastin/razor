"""
Layer 2 — Behavioral Anomaly Detection Engine.

Scores session-level behavioral features for transactions that pass Layer 1
deterministic verification. Detects non-human invocation, token replay,
timing anomalies, and velocity drift.
"""

from layer2_detector.cost_model import CostModel, CostOptimizationResult
from layer2_detector.features import (
    CANONICAL_SEQUENCE,
    FEATURE_COLUMNS,
    extract_all_features,
)
from layer2_detector.model import BehavioralRiskModel, BehavioralRiskResult

__all__ = [
    "BehavioralRiskModel",
    "BehavioralRiskResult",
    "CostModel",
    "CostOptimizationResult",
    "extract_all_features",
    "FEATURE_COLUMNS",
    "CANONICAL_SEQUENCE",
]
