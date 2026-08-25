"""
Layer 1 — Batch runner for the simulated dataset.

Loads mandates + transactions from Parquet, runs verify_transaction
against each, and produces per-attack-class catch rates.

Usage:
    python -m layer1_verifier.batch_runner [--data-dir simulator/data]
"""

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
import structlog

sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_config import setup_logging
from layer1_verifier.checks import verify_transaction

logger = structlog.get_logger(__name__)


def load_data(data_dir: str) -> tuple:
    """Load mandates and transactions from Parquet."""
    data_path = Path(data_dir)
    mandates_df = pd.read_parquet(data_path / "mandates.parquet")
    transactions_df = pd.read_parquet(data_path / "transactions.parquet")
    return mandates_df, transactions_df


def build_mandate_lookup(mandates_df: pd.DataFrame) -> dict:
    """Build a dict of mandate_id -> mandate record."""
    lookup = {}
    for _, row in mandates_df.iterrows():
        record = row.to_dict()
        # Parse JSON string lists back into Python lists
        for col in ("permitted_mccs", "permitted_categories", "beneficiaries"):
            val = record.get(col)
            if isinstance(val, str):
                try:
                    record[col] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        lookup[record["mandate_id"]] = record
    return lookup


def build_key_registry(mandates_df: pd.DataFrame) -> dict:
    """Build a simulated key registry: mandate_id -> primary_agent_type."""
    return dict(zip(mandates_df["mandate_id"], mandates_df["primary_agent_type"]))


def run_batch(data_dir: str = "simulator/data") -> dict:
    """
    Run Layer 1 verification against the full simulated transaction set.

    Returns a summary dict with catch rates by attack class.
    """
    setup_logging("INFO")

    logger.info("batch_runner_starting", data_dir=data_dir)

    mandates_df, transactions_df = load_data(data_dir)
    mandate_lookup = build_mandate_lookup(mandates_df)
    key_registry = build_key_registry(mandates_df)
    known_mandate_ids = set(mandates_df["mandate_id"])

    # Build per-mandate recent transaction history for cadence checks
    # Sort by timestamp and group by mandate_id
    sorted_txns = transactions_df.sort_values("timestamp")
    mandate_txn_history: dict = defaultdict(list)

    total = len(transactions_df)
    results_list = []

    logger.info("processing_transactions", total=total)

    for idx, (_, txn) in enumerate(sorted_txns.iterrows()):
        txn_dict = txn.to_dict()
        # Parse JSON string lists
        for col in ("cart_items",):
            val = txn_dict.get(col)
            if isinstance(val, str):
                try:
                    txn_dict[col] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass

        mandate_id = txn_dict.get("mandate_id")
        mandate = mandate_lookup.get(mandate_id)

        if mandate is None:
            # Unknown mandate — will fail V5
            mandate = {}

        # Get recent transaction timestamps for this mandate (cadence check)
        recent_timestamps = mandate_txn_history.get(mandate_id, [])

        # Run verification
        result = verify_transaction(
            transaction=txn_dict,
            mandate=mandate,
            known_mandate_ids=known_mandate_ids,
            key_registry=key_registry,
            recent_txn_timestamps=recent_timestamps[-10:],  # last 10
        )

        # Record result
        results_list.append({
            "transaction_id": txn_dict.get("transaction_id"),
            "mandate_id": mandate_id,
            "label": txn_dict.get("label"),
            "attack_class": txn_dict.get("attack_class"),
            "hard_negative_type": txn_dict.get("hard_negative_type"),
            "l1_verdict": result.verdict,
            "l1_failed_checks": result.failed_checks,
        })

        # Update history for cadence tracking
        ts = txn_dict.get("timestamp")
        if ts and mandate_id:
            mandate_txn_history[mandate_id].append(ts)

        if (idx + 1) % 10000 == 0:
            logger.info("progress", processed=idx + 1, total=total)

    logger.info("batch_complete", total=total)

    # ── Analysis ─────────────────────────────────────────────────
    results_df = pd.DataFrame(results_list)

    # Overall stats
    total_pass = (results_df["l1_verdict"] == "pass").sum()
    total_fail = (results_df["l1_verdict"] == "fail").sum()

    # Attack catch rates by class
    attack_df = results_df[results_df["label"] == "attack"]
    attack_stats = {}
    for attack_class in sorted(attack_df["attack_class"].unique()):
        cls_df = attack_df[attack_df["attack_class"] == attack_class]
        caught = (cls_df["l1_verdict"] == "fail").sum()
        total_cls = len(cls_df)
        attack_stats[attack_class] = {
            "total": int(total_cls),
            "caught": int(caught),
            "missed": int(total_cls - caught),
            "catch_rate": round(caught / max(total_cls, 1) * 100, 2),
        }

    # Legitimate false positive rate
    legit_df = results_df[results_df["label"] == "legitimate"]
    legit_flagged = (legit_df["l1_verdict"] == "fail").sum()
    legit_total = len(legit_df)

    # Hard negative false positive rate
    hn_df = results_df[results_df["hard_negative_type"] != "none"]
    hn_flagged = (hn_df["l1_verdict"] == "fail").sum()
    hn_total = len(hn_df)

    # Most common failed checks
    all_failed = []
    for fcs in results_df["l1_failed_checks"]:
        if isinstance(fcs, list):
            all_failed.extend(fcs)
    check_freq = pd.Series(all_failed).value_counts().to_dict() if all_failed else {}

    summary = {
        "total_transactions": int(total),
        "total_pass": int(total_pass),
        "total_fail": int(total_fail),
        "attack_catch_rates": attack_stats,
        "legitimate_false_positives": {
            "total_legitimate": int(legit_total),
            "flagged": int(legit_flagged),
            "false_positive_rate": round(
                legit_flagged / max(legit_total, 1) * 100, 2
            ),
        },
        "hard_negative_false_positives": {
            "total_hard_negatives": int(hn_total),
            "flagged": int(hn_flagged),
            "false_positive_rate": round(
                hn_flagged / max(hn_total, 1) * 100, 2
            ),
        },
        "failed_check_frequency": check_freq,
    }

    # Print the report
    print_report(summary)

    return summary


def print_report(summary: dict):
    """Print a human-readable Layer 1 evaluation report."""
    print("\n" + "=" * 70)
    print("  LAYER 1 — MANDATE VERIFIER — Evaluation Report")
    print("=" * 70)

    print(f"\n📊 OVERALL: {summary['total_transactions']} transactions")
    print(f"   Pass: {summary['total_pass']}  |  Fail: {summary['total_fail']}")

    print(f"\n🎯 ATTACK CATCH RATES BY CLASS:")
    print(f"   {'Class':<30} {'Total':>6} {'Caught':>7} {'Missed':>7} {'Rate':>8}")
    print(f"   {'─' * 58}")
    for cls, stats in sorted(summary["attack_catch_rates"].items()):
        bar = "█" * int(stats["catch_rate"] / 5)
        print(
            f"   {cls:<30} {stats['total']:>6} {stats['caught']:>7} "
            f"{stats['missed']:>7} {stats['catch_rate']:>7.1f}% {bar}"
        )

    fp = summary["legitimate_false_positives"]
    print(f"\n⚠️  LEGITIMATE FALSE POSITIVES:")
    print(f"   {fp['flagged']} / {fp['total_legitimate']} = {fp['false_positive_rate']}%")

    hn = summary["hard_negative_false_positives"]
    print(f"\n🔍 HARD NEGATIVE FALSE POSITIVES:")
    print(f"   {hn['flagged']} / {hn['total_hard_negatives']} = {hn['false_positive_rate']}%")

    print(f"\n📋 FAILED CHECK FREQUENCY:")
    for check, count in sorted(
        summary["failed_check_frequency"].items(),
        key=lambda x: -x[1],
    ):
        print(f"   {check}: {count}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Layer 1 Batch Evaluation")
    parser.add_argument(
        "--data-dir", type=str, default="simulator/data",
        help="Directory containing Parquet data files",
    )
    args = parser.parse_args()
    run_batch(args.data_dir)
