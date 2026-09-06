"""Frozen-holdout evaluation for Layer 3's individual A6 signals."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from layer3_intent.detector import IntentIntegrityDetector, cart_description


def _records(df: pd.DataFrame) -> list[dict]:
    return df.to_dict("records")


def _metric(rows: list[dict], signal: str) -> dict:
    positives = [r for r in rows if r["attack_class"] == "A6_injected_intent"]
    flagged = [r for r in rows if signal in r["signals"]]
    tp = sum(r["attack_class"] == "A6_injected_intent" for r in flagged)
    return {
        "tp": int(tp), "flagged": len(flagged), "a6_total": len(positives),
        "precision": round(tp / len(flagged), 4) if flagged else 0.0,
        "recall": round(tp / len(positives), 4) if positives else 0.0,
    }


def choose_i1_threshold(detector: IntentIntegrityDetector, mandates: pd.DataFrame, train: pd.DataFrame) -> float:
    """Calibrate on training mandates only; preserve the frozen holdout for reporting."""
    lookup = {r["mandate_id"]: r for r in _records(mandates)}
    records = _records(train)
    # Batch embedding is materially faster than calling the vectorizer for each
    # row and uses no labels in the representation itself.
    purposes = [str(lookup[txn["mandate_id"]]["purpose"]) for txn in records]
    carts = [cart_description(txn) for txn in records]
    purpose_vectors = detector.embedder.encode(purposes)
    cart_vectors = detector.embedder.encode(carts)
    norms = np.linalg.norm(purpose_vectors, axis=1) * np.linalg.norm(cart_vectors, axis=1)
    similarities = np.divide((purpose_vectors * cart_vectors).sum(axis=1), norms, out=np.zeros(len(records)), where=norms != 0)
    scores = list(zip(similarities, (txn["attack_class"] == "A6_injected_intent" for txn in records)))
    best_threshold, best_f1 = detector.i1_threshold, -1.0
    # A bounded grid makes calibration reproducible and avoids an O(n²)
    # sweep over every distinct floating-point score.
    for threshold in np.unique(np.quantile(similarities, np.linspace(0.0, 1.0, 201))):
        tp = sum(is_a6 and score < threshold for score, is_a6 in scores)
        fp = sum((not is_a6) and score < threshold for score, is_a6 in scores)
        fn = sum(is_a6 and score >= threshold for score, is_a6 in scores)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if f1 > best_f1:
            best_threshold, best_f1 = threshold, f1
    return float(best_threshold)


def evaluate_layer3(data_dir: str = "simulator/data", split_path: str = "results/holdout_split.json", output_path: str = "results/layer3_evaluation.json") -> dict:
    data = Path(data_dir)
    mandates = pd.read_parquet(data / "mandates.parquet")
    transactions = pd.read_parquet(data / "transactions.parquet").sort_values("timestamp")
    split = json.loads(Path(split_path).read_text())
    holdout_ids = set(split["holdout"]["mandate_ids"])
    train = transactions[~transactions.mandate_id.isin(holdout_ids)]
    holdout = transactions[transactions.mandate_id.isin(holdout_ids)]
    mandate_lookup = {r["mandate_id"]: r for r in _records(mandates)}

    detector = IntentIntegrityDetector().fit(_records(mandates[~mandates.mandate_id.isin(holdout_ids)]), _records(train))
    detector.i1_threshold = choose_i1_threshold(detector, mandates, train)

    history: dict[str, list[dict]] = defaultdict(list)
    output_rows = []
    holdout_records = _records(holdout)
    purposes = [str(mandate_lookup[txn["mandate_id"]]["purpose"]) for txn in holdout_records]
    carts = [cart_description(txn) for txn in holdout_records]
    purpose_vectors = detector.embedder.encode(purposes)
    cart_vectors = detector.embedder.encode(carts)
    norms = np.linalg.norm(purpose_vectors, axis=1) * np.linalg.norm(cart_vectors, axis=1)
    similarities = np.divide((purpose_vectors * cart_vectors).sum(axis=1), norms, out=np.zeros(len(holdout_records)), where=norms != 0)
    # Mandates are disjoint between train and holdout, so only earlier events
    # for a holdout mandate can affect its beneficiary/timing history.
    for txn, similarity in zip(holdout_records, similarities):
        i4 = detector._i4_evidence(txn, history[txn["mandate_id"]])
        signals = ([] if similarity >= detector.i1_threshold else ["I1"]) + (["I4"] if i4["triggered"] else [])
        output_rows.append({"attack_class": txn["attack_class"], "signals": signals})
        history[txn["mandate_id"]].append(txn)

    report = {
        "evaluation_split": "frozen_holdout",
        "i1_threshold_calibrated_on": "training_mandates_only",
        "i1_threshold": detector.i1_threshold,
        "embedding_backend": detector.embedder.backend,
        "A6_specific_metrics": {"I1": _metric(output_rows, "I1"), "I4": _metric(output_rows, "I4")},
    }
    Path(output_path).write_text(json.dumps(report, indent=2) + "\n")
    print("\n" + "=" * 70)
    print("  LAYER 3 — INTENT INTEGRITY — Frozen Holdout Evaluation")
    print("=" * 70)
    print(f"\nI1 threshold (calibrated on training only): {detector.i1_threshold:.4f}")
    print(f"Embedding backend: {detector.embedder.backend}")
    print("\nA6-SPECIFIC INDIVIDUAL SIGNAL METRICS:")
    print(f"   {'Signal':<8} {'Precision':>11} {'Recall':>10} {'TP':>6} {'Flagged':>9} {'A6 total':>10}")
    for signal, metric in report["A6_specific_metrics"].items():
        print(f"   {signal:<8} {metric['precision'] * 100:>10.2f}% {metric['recall'] * 100:>9.2f}% {metric['tp']:>6} {metric['flagged']:>9} {metric['a6_total']:>10}")
    print("=" * 70 + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Evaluate Layer 3 against the frozen holdout")
    parser.add_argument("--data-dir", default="simulator/data")
    parser.add_argument("--split-path", default="results/holdout_split.json")
    parser.add_argument("--output", default="results/layer3_evaluation.json")
    args = parser.parse_args()
    evaluate_layer3(args.data_dir, args.split_path, args.output)
