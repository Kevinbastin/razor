"""
Layer 2 — Behavioral Risk Scoring Model.

Wraps a LightGBM gradient boosted classifier with structured risk scoring,
feature attribution, model persistence, and fail-closed evaluation.
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import joblib
import hashlib
import json
import lightgbm as lgb
import numpy as np
import pandas as pd
import structlog
from observability.metrics import METRICS

from layer2_detector.features import FEATURE_COLUMNS

logger = structlog.get_logger(__name__)


@dataclass
class RiskFactor:
    """A single high-contributing risk feature."""
    feature: str
    value: float
    importance_rank: int
    direction: str  # "high" | "low" | "abnormal"


@dataclass
class BehavioralRiskResult:
    """
    Structured output of Layer 2 risk scoring.

    Conforms to the project-wide structured JSON reason trail design (Design Principle #2).
    """
    risk_score: float
    verdict: str  # "pass" | "suspicious" | "attack"
    threshold: float
    top_risk_factors: List[Dict[str, Any]]
    evidence: Dict[str, Any]
    model_version: str = "1.0.0"

    def to_dict(self) -> dict:
        return asdict(self)


class BehavioralRiskModel:
    """
    Gradient-boosted behavioral anomaly classifier for Layer 2.
    """

    def __init__(
        self,
        n_estimators: int = 200,
        learning_rate: float = 0.05,
        max_depth: int = 6,
        num_leaves: int = 31,
        min_child_samples: int = 20,
        scale_pos_weight: float = 5.0,
        random_state: int = 42,
        **kwargs,
    ):
        self.params = {
            "n_estimators": n_estimators,
            "learning_rate": learning_rate,
            "max_depth": max_depth,
            "num_leaves": num_leaves,
            "min_child_samples": min_child_samples,
            "scale_pos_weight": scale_pos_weight,
            "random_state": random_state,
            "objective": kwargs.get("objective", "binary"),
            "verbose": kwargs.get("verbose", -1),
            "n_jobs": kwargs.get("n_jobs", -1),
        }
        self.model: Optional[lgb.LGBMClassifier] = None
        self.feature_names = list(FEATURE_COLUMNS)
        self.is_fitted = False

    def fit(
        self,
        X_train: pd.DataFrame,
        y_train: np.ndarray,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[np.ndarray] = None,
    ) -> "BehavioralRiskModel":
        """
        Train the LightGBM classifier on extracted features.
        """
        # Ensure only feature columns are used
        X_tr = X_train[self.feature_names].copy()

        self.model = lgb.LGBMClassifier(**self.params)

        if X_val is not None and y_val is not None:
            X_v = X_val[self.feature_names].copy()
            self.model.fit(
                X_tr,
                y_train,
                eval_set=[(X_v, y_val)],
                eval_metric="auc",
                callbacks=[lgb.early_stopping(stopping_rounds=40, verbose=False)],
            )
        else:
            self.model.fit(X_tr, y_train)

        self.is_fitted = True
        logger.info(
            "layer2_model_fitted",
            n_features=len(self.feature_names),
            n_train_samples=len(X_train),
        )
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """
        Predict probability of attack for a feature matrix.
        """
        if not self.is_fitted or self.model is None:
            raise RuntimeError("Model is not fitted yet.")

        X_feat = X[self.feature_names].copy()
        probs = self.model.predict_proba(X_feat)[:, 1]
        return probs

    def score_single(
        self,
        features: Dict[str, Any],
        threshold: float = 0.5,
        suspicious_threshold: float = 0.25,
    ) -> BehavioralRiskResult:
        """
        Score a single transaction's feature dictionary and return structured result.
        """
        if not self.is_fitted or self.model is None:
            # Fail closed
            return BehavioralRiskResult(
                risk_score=1.0,
                verdict="attack",
                threshold=threshold,
                top_risk_factors=[{"feature": "model_status", "value": 0.0, "reason": "model_not_fitted"}],
                evidence={"error": "model_not_fitted"},
            )

        # Build single-row DataFrame
        row_dict = {col: float(features.get(col, 0.0)) for col in self.feature_names}
        df_row = pd.DataFrame([row_dict])

        prob = float(self.predict_proba(df_row)[0])

        if prob >= threshold:
            verdict = "attack"
        elif prob >= suspicious_threshold:
            verdict = "suspicious"
        else:
            verdict = "pass"

        # Determine top risk factors based on feature values & feature importances
        top_factors = self._explain_single(row_dict)

        evidence = {
            "risk_score": round(prob, 4),
            "threshold": threshold,
            "verdict": verdict,
            "features_summary": {
                "seq_skip_ahead": features.get("seq_skip_ahead", 0),
                "timing_latency_cv": features.get("timing_latency_cv", 0.0),
                "cred_token_reused": features.get("cred_token_reused", 0),
                "vel_burst_score": features.get("vel_burst_score", 0.0),
                "vel_amount_z_score": features.get("vel_amount_z_score", 0.0),
            },
        }

        output = BehavioralRiskResult(
            risk_score=round(prob, 4),
            verdict=verdict,
            threshold=threshold,
            top_risk_factors=top_factors,
            evidence=evidence,
        )
        logger.info("layer2_decision", verdict=output.verdict, risk_score=output.risk_score, threshold=threshold)
        METRICS.record("layer2", output.verdict)
        return output

    def _explain_single(self, row_dict: Dict[str, float], top_k: int = 4) -> List[Dict[str, Any]]:
        """Identify top contributing features for a single sample."""
        if not self.is_fitted or self.model is None:
            return []

        importances = self.model.feature_importances_
        sorted_indices = np.argsort(importances)[::-1]

        factors = []
        for rank, idx in enumerate(sorted_indices[:top_k], 1):
            feat_name = self.feature_names[idx]
            val = row_dict.get(feat_name, 0.0)
            factors.append({
                "feature": feat_name,
                "value": round(float(val), 4),
                "importance_rank": rank,
                "feature_importance": int(importances[idx]),
            })
        return factors

    def get_feature_importances(self) -> pd.DataFrame:
        """Return DataFrame of feature importances sorted descending."""
        if not self.is_fitted or self.model is None:
            raise RuntimeError("Model is not fitted yet.")

        df = pd.DataFrame({
            "feature": self.feature_names,
            "importance": self.model.feature_importances_,
        }).sort_values("importance", ascending=False).reset_index(drop=True)
        return df

    def save(self, output_path: Union[str, Path], *, data_snapshot_version: str = "unknown") -> None:
        """Save model artifact to disk."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self.model,
                "params": self.params,
                "feature_names": self.feature_names,
                "is_fitted": self.is_fitted,
            },
            output_path,
        )
        metadata = {
            "model_version": "1.0.0", "feature_set_version": hashlib.sha256("|".join(self.feature_names).encode()).hexdigest()[:12],
            "data_snapshot_version": data_snapshot_version,
            "feature_columns": self.feature_names,
        }
        Path(str(output_path) + ".metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        logger.info("layer2_model_saved", path=str(output_path))

    @classmethod
    def load(cls, model_path: Union[str, Path]) -> "BehavioralRiskModel":
        """Load trained model artifact from disk."""
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(f"Model file not found: {model_path}")

        data = joblib.load(model_path)
        instance = cls(**data.get("params", {}))
        instance.model = data["model"]
        instance.feature_names = data["feature_names"]
        instance.is_fitted = data["is_fitted"]
        logger.info("layer2_model_loaded", path=str(model_path))
        return instance
