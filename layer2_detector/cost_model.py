"""
Layer 2 — Cost-Optimal Decision Threshold Tuning.

In high-stakes fraud detection, the default 0.5 classification threshold is almost
never optimal. False Positives (blocking a legitimate agent payment, causing user friction)
and False Negatives (allowing an attack, causing direct fraud loss) have asymmetric costs.

This module searches for the decision threshold that minimizes total business cost:
    Total Cost = (FP_count * cost_fp) + (FN_count * cost_fn)
"""

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class CostOptimizationResult:
    """Results of threshold cost optimization."""
    optimal_threshold: float
    min_cost: float
    cost_at_default_05: float
    cost_reduction_pct: float
    cost_fp: float
    cost_fn: float
    optimal_metrics: Dict[str, float]
    default_metrics: Dict[str, float]
    threshold_curve: List[Dict[str, float]]

    def to_dict(self) -> dict:
        return asdict(self)


class CostModel:
    """
    Cost-sensitive threshold optimizer for fraud decisioning.
    """

    def __init__(
        self,
        cost_fp: float = 100.0,   # Cost of blocking a legitimate transaction (user friction, churn)
        cost_fn: float = 2500.0,  # Cost of a missed attack (direct fraudulent loss, chargeback)
    ):
        self.cost_fp = float(cost_fp)
        self.cost_fn = float(cost_fn)

    def optimize_threshold(
        self,
        y_true: np.ndarray,
        y_prob: np.ndarray,
        threshold_steps: int = 100,
    ) -> CostOptimizationResult:
        """
        Find the threshold t* in [0.01, 0.99] that minimizes expected business cost.
        """
        y_true = np.asarray(y_true).astype(int)
        y_prob = np.asarray(y_prob).astype(float)

        thresholds = np.linspace(0.01, 0.99, threshold_steps)

        best_threshold = 0.5
        min_cost = float("inf")
        best_metrics = {}

        curve = []
        default_metrics = {}

        for t in thresholds:
            y_pred = (y_prob >= t).astype(int)

            tp = int(((y_pred == 1) & (y_true == 1)).sum())
            fp = int(((y_pred == 1) & (y_true == 0)).sum())
            tn = int(((y_pred == 0) & (y_true == 0)).sum())
            fn = int(((y_pred == 0) & (y_true == 1)).sum())

            cost = (fp * self.cost_fp) + (fn * self.cost_fn)

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
            fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

            metrics = {
                "threshold": round(float(t), 3),
                "total_cost": round(float(cost), 2),
                "tp": tp,
                "fp": fp,
                "tn": tn,
                "fn": fn,
                "precision": round(float(precision), 4),
                "recall": round(float(recall), 4),
                "f1": round(float(f1), 4),
                "fpr": round(float(fpr), 4),
            }
            curve.append(metrics)

            if abs(t - 0.5) < 0.01 and not default_metrics:
                default_metrics = metrics

            if cost < min_cost:
                min_cost = cost
                best_threshold = float(t)
                best_metrics = metrics

        if not default_metrics:
            # Fallback if 0.5 wasn't hit exactly
            default_pred = (y_prob >= 0.5).astype(int)
            tp_d = int(((default_pred == 1) & (y_true == 1)).sum())
            fp_d = int(((default_pred == 1) & (y_true == 0)).sum())
            tn_d = int(((default_pred == 0) & (y_true == 0)).sum())
            fn_d = int(((default_pred == 0) & (y_true == 1)).sum())
            cost_d = (fp_d * self.cost_fp) + (fn_d * self.cost_fn)
            prec_d = tp_d / (tp_d + fp_d) if (tp_d + fp_d) > 0 else 0.0
            rec_d = tp_d / (tp_d + fn_d) if (tp_d + fn_d) > 0 else 0.0
            f1_d = 2 * prec_d * rec_d / (prec_d + rec_d) if (prec_d + rec_d) > 0 else 0.0
            default_metrics = {
                "threshold": 0.5,
                "total_cost": round(float(cost_d), 2),
                "tp": tp_d,
                "fp": fp_d,
                "tn": tn_d,
                "fn": fn_d,
                "precision": round(float(prec_d), 4),
                "recall": round(float(rec_d), 4),
                "f1": round(float(f1_d), 4),
                "fpr": round(float(fp_d / (fp_d + tn_d)), 4),
            }

        cost_05 = default_metrics["total_cost"]
        savings = ((cost_05 - min_cost) / cost_05 * 100) if cost_05 > 0 else 0.0

        return CostOptimizationResult(
            optimal_threshold=round(best_threshold, 3),
            min_cost=round(min_cost, 2),
            cost_at_default_05=round(cost_05, 2),
            cost_reduction_pct=round(savings, 2),
            cost_fp=self.cost_fp,
            cost_fn=self.cost_fn,
            optimal_metrics=best_metrics,
            default_metrics=default_metrics,
            threshold_curve=curve,
        )
