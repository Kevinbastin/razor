#!/usr/bin/env python3
"""Pitch-demo: show A6 attacks surviving Layers 1/2 and stopped by Layer 3.

Run from the repository root:
    python3 scripts/demo_a6_catch.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from layer1_verifier.checks import verify_transaction
from layer2_detector.features import extract_all_features
from layer2_detector.model import BehavioralRiskModel
from layer3_intent.detector import IntentIntegrityDetector, cart_description


def main() -> None:
    data_dir = ROOT / "simulator/data"
    mandates = pd.read_parquet(data_dir / "mandates.parquet")
    transactions = pd.read_parquet(data_dir / "transactions.parquet").sort_values("timestamp")
    events = pd.read_parquet(data_dir / "session_events.parquet")
    split = json.loads((ROOT / "results/holdout_split.json").read_text())
    holdout_ids = set(split["holdout"]["mandate_ids"])
    mandate_lookup = {r["mandate_id"]: r for r in mandates.to_dict("records")}
    known_ids = set(mandate_lookup)
    key_registry = {r["mandate_id"]: r["primary_agent_type"] for r in mandates.to_dict("records")}

    train = transactions[~transactions.mandate_id.isin(holdout_ids)]
    holdout = transactions[transactions.mandate_id.isin(holdout_ids)].copy()
    detector = IntentIntegrityDetector().fit(
        mandates[~mandates.mandate_id.isin(holdout_ids)].to_dict("records"), train.to_dict("records")
    )
    evaluation_path = ROOT / "results/layer3_evaluation.json"
    if evaluation_path.exists():
        detector.i1_threshold = float(json.loads(evaluation_path.read_text())["i1_threshold"])
    else:
        # The checked-in evaluator artifact is normally present. This fallback
        # remains conservative for a fresh simulator run; rerun the evaluator
        # to calibrate a new data version before presenting the demo.
        detector.i1_threshold = 0.021

    # Demo only needs a frozen-holdout mandate's own prior activity; mandates
    # are disjoint across splits, so this preserves the live scoring history.
    session_ids = set(holdout["session_id"].dropna())
    features = extract_all_features(holdout, events[events.session_id.isin(session_ids)], mandates)
    feature_lookup = {r["transaction_id"]: r for r in features.to_dict("records")}
    model = BehavioralRiskModel.load(ROOT / "results/layer2_model.joblib")

    l1_history: dict[str, list[str]] = defaultdict(list)
    l3_history: dict[str, list[dict]] = defaultdict(list)
    candidates = []
    for txn in holdout.to_dict("records"):
        mandate_id = txn["mandate_id"]
        if txn["attack_class"] == "A6_injected_intent":
            l1 = verify_transaction(txn, mandate_lookup[mandate_id], known_ids, key_registry, l1_history[mandate_id][-10:])
            l2 = model.score_single(feature_lookup[txn["transaction_id"]])
            l3 = detector.score_transaction(txn, mandate_lookup[mandate_id], l3_history[mandate_id])
            if l1.verdict == "pass" and l3.verdict == "flagged":
                candidates.append((txn, l1, l2, l3))
        l1_history[mandate_id].append(txn["timestamp"])
        l3_history[mandate_id].append(txn)

    # Prefer A6 samples whose behavioral score passes, demonstrating that Layer
    # 3 catches goal hijack after authority and behavior have looked normal.
    candidates.sort(key=lambda row: (row[2].risk_score >= 0.5, row[2].risk_score))
    samples = candidates[:5]
    if not samples:
        raise RuntimeError("No A6 samples passed Layer 1 and triggered Layer 3.")

    print("\n" + "═" * 112)
    print("  A6 PROMPT-INJECTION CATCH DEMO — Authority intact. Behavior normal. Goal hijacked.")
    print("═" * 112)
    print("  Layer 1 checks mandate authority.  Layer 2 checks session behavior.  Layer 3 checks purchased outcome vs. stated goal.\n")
    print(f"  {'Transaction':<18} {'Cart (hijacked outcome)':<37} {'L1 authority':<16} {'L2 behavior':<20} {'L3 intent':<18}")
    print("  " + "─" * 108)
    for txn, l1, l2, l3 in samples:
        cart = cart_description(txn).split(". Merchant category:", 1)[0].replace("Cart contents: ", "")[:35]
        l1_label = "PASS ✓" if l1.verdict == "pass" else f"FAIL ({','.join(l1.failed_checks)})"
        l2_label = f"{l2.verdict.upper()} ({l2.risk_score:.2f})"
        evidence = l3.evidence["I1"]
        signals = "+".join(l3.signals_triggered)
        l3_label = f"FLAGGED {signals} ({evidence['similarity_score']:.2f})"
        print(f"  {txn['transaction_id']:<18} {cart:<37} {l1_label:<16} {l2_label:<20} {l3_label:<18}")

    print("\n  Why Layer 3 flagged these:")
    first_txn, _, _, first_l3 = samples[0]
    i1 = first_l3.evidence["I1"]
    i4 = first_l3.evidence["I4"]
    print(f"  • I1 semantic divergence: mandate purpose = {i1['purpose']!r}; similarity {i1['similarity_score']:.2f} < threshold {i1['threshold']:.2f}.")
    print(f"  • I4 escalation triple: first-time={i4['first_time_beneficiary']}, high-value={i4['upper_quartile_value']}, off-pattern timing={i4['timing_deviates_from_pattern']}; it only triggers when all three are true.")
    print("  • Result: Layer 3 blocks the payment for review with a concrete, auditable reason trail.")
    print("═" * 112 + "\n")


if __name__ == "__main__":
    main()
