"""Fail a promotion check when numeric feature distributions materially drift.

Usage: python scripts/check_feature_drift.py baseline.csv candidate.csv
The CSVs must contain the same numeric feature columns. A PSI above the
configured threshold is reported and exits non-zero; this is a gate, not an
automatic retraining decision.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd


def population_stability_index(baseline: pd.Series, candidate: pd.Series, bins: int = 10) -> float:
    base = pd.to_numeric(baseline, errors="coerce").dropna()
    current = pd.to_numeric(candidate, errors="coerce").dropna()
    if base.empty or current.empty:
        raise ValueError("PSI requires non-empty numeric samples")
    edges = np.unique(np.quantile(base, np.linspace(0, 1, bins + 1)))
    if len(edges) < 2:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    expected = np.histogram(base, bins=edges)[0] / len(base)
    actual = np.histogram(current, bins=edges)[0] / len(current)
    expected = np.clip(expected, 1e-6, None)
    actual = np.clip(actual, 1e-6, None)
    return float(np.sum((actual - expected) * np.log(actual / expected)))


def assess_drift(baseline: pd.DataFrame, candidate: pd.DataFrame, threshold: float = 0.20) -> dict:
    columns = sorted(set(baseline.columns) & set(candidate.columns))
    numeric = [c for c in columns if pd.api.types.is_numeric_dtype(baseline[c]) and pd.api.types.is_numeric_dtype(candidate[c])]
    if not numeric:
        raise ValueError("No shared numeric columns to evaluate")
    psi = {column: round(population_stability_index(baseline[column], candidate[column]), 6) for column in numeric}
    flagged = {column: score for column, score in psi.items() if score > threshold}
    return {"threshold": threshold, "features": psi, "flagged_features": flagged, "verdict": "fail" if flagged else "pass"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline")
    parser.add_argument("candidate")
    parser.add_argument("--threshold", type=float, default=0.20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = assess_drift(pd.read_csv(args.baseline), pd.read_csv(args.candidate), args.threshold)
    rendered = json.dumps(result, indent=2)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n")
    return 1 if result["verdict"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
