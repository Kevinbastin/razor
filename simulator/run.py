"""
Simulator orchestrator — generates all data and writes to disk.

Usage:
    python -m simulator.run [--output-dir simulator/data] [--format parquet]

Outputs:
    - mandates.parquet
    - transactions.parquet
    - session_events.parquet
    - results/holdout_split.json

Design principles enforced:
    - Seeded RNG for full reproducibility
    - Config-driven, versioned
    - Held-out split frozen at creation time (20% of mandates)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_config import setup_logging
from simulator.config import SimulatorConfig
from simulator.mandate_generator import generate_mandates
from simulator.transaction_generator import generate_transactions

IST = timezone(timedelta(hours=5, minutes=30))

logger = structlog.get_logger(__name__)


def create_holdout_split(
    mandates_df: pd.DataFrame,
    transactions_df: pd.DataFrame,
    config: SimulatorConfig,
    output_path: Path,
) -> dict:
    """
    Create a held-out test split based on mandate IDs (not transaction IDs)
    to avoid data leakage across a mandate's transaction history.

    Writes to results/holdout_split.json and returns the split metadata.
    """
    rng = np.random.default_rng(config.seed + 100)  # Separate seed for split

    mandate_ids = mandates_df["mandate_id"].unique()
    n_holdout = int(len(mandate_ids) * config.holdout_fraction)

    # Shuffle and split
    shuffled = rng.permutation(mandate_ids)
    holdout_mandate_ids = list(shuffled[:n_holdout])
    train_mandate_ids = list(shuffled[n_holdout:])

    # Count transactions in each split
    holdout_txn_mask = transactions_df["mandate_id"].isin(holdout_mandate_ids)
    train_txn_mask = ~holdout_txn_mask

    # Count attacks in each split
    holdout_attacks = int(
        (transactions_df[holdout_txn_mask]["label"] == "attack").sum()
    )
    train_attacks = int(
        (transactions_df[train_txn_mask]["label"] == "attack").sum()
    )

    split_metadata = {
        "version": config.version,
        "seed": config.seed,
        "created_at": datetime.now(IST).isoformat(),
        "holdout_fraction": config.holdout_fraction,
        "WARNING": (
            "DO NOT regenerate or touch this split after creation. "
            "The held-out evaluation split is frozen. See Design Principle #4."
        ),
        "holdout": {
            "mandate_ids": holdout_mandate_ids,
            "n_mandates": len(holdout_mandate_ids),
            "n_transactions": int(holdout_txn_mask.sum()),
            "n_attacks": holdout_attacks,
            "attack_rate": round(
                holdout_attacks / max(int(holdout_txn_mask.sum()), 1) * 100, 2
            ),
        },
        "train": {
            "mandate_ids": train_mandate_ids,
            "n_mandates": len(train_mandate_ids),
            "n_transactions": int(train_txn_mask.sum()),
            "n_attacks": train_attacks,
            "attack_rate": round(
                train_attacks / max(int(train_txn_mask.sum()), 1) * 100, 2
            ),
        },
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(split_metadata, f, indent=2)

    logger.info(
        "holdout_split_created",
        holdout_mandates=len(holdout_mandate_ids),
        holdout_transactions=int(holdout_txn_mask.sum()),
        train_mandates=len(train_mandate_ids),
        train_transactions=int(train_txn_mask.sum()),
        output_path=str(output_path),
    )

    return split_metadata


def _serialize_list_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Convert list columns to JSON strings for Parquet compatibility."""
    df = df.copy()
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (list, dict))).any():
            df[col] = df[col].apply(
                lambda x: json.dumps(x) if isinstance(x, (list, dict)) else x
            )
    return df


def print_stats(
    mandates_df: pd.DataFrame,
    transactions_df: pd.DataFrame,
    events_df: pd.DataFrame,
    split_metadata: dict,
):
    """Print comprehensive generation statistics."""
    print("\n" + "=" * 70)
    print("  AGENT TRANSACTION RISK LAYER — Simulator Output Statistics")
    print("=" * 70)

    print(f"\n📋 MANDATES: {len(mandates_df)}")
    print(f"   Lifecycle:  {mandates_df['lifecycle_state'].value_counts().to_dict()}")
    print(f"   Cadence:    {mandates_df['cadence'].value_counts().to_dict()}")
    print(f"   Agent type: {mandates_df['primary_agent_type'].value_counts().to_dict()}")
    print(f"   Avg ceiling: ₹{mandates_df['amount_ceiling'].mean():.0f}")
    print(f"   Median ceiling: ₹{mandates_df['amount_ceiling'].median():.0f}")

    print(f"\n💳 TRANSACTIONS: {len(transactions_df)}")
    n_attacks = (transactions_df["label"] == "attack").sum()
    n_legit = (transactions_df["label"] == "legitimate").sum()
    n_hn = (transactions_df["hard_negative_type"] != "none").sum()
    print(f"   Legitimate: {n_legit} ({n_legit/len(transactions_df)*100:.1f}%)")
    print(f"   Attacks:    {n_attacks} ({n_attacks/len(transactions_df)*100:.2f}%)")
    print(f"   Hard negatives: {n_hn} ({n_hn/len(transactions_df)*100:.2f}%)")

    print(f"\n🎯 ATTACK CLASS DISTRIBUTION:")
    attack_counts = transactions_df[
        transactions_df["label"] == "attack"
    ]["attack_class"].value_counts()
    for cls, count in attack_counts.items():
        pct = count / n_attacks * 100
        bar = "█" * int(pct / 2)
        print(f"   {cls:30s} {count:5d}  ({pct:5.1f}%) {bar}")

    print(f"\n🔍 HARD NEGATIVE DISTRIBUTION:")
    hn_counts = transactions_df[
        transactions_df["hard_negative_type"] != "none"
    ]["hard_negative_type"].value_counts()
    for hn_type, count in hn_counts.items():
        print(f"   {hn_type:30s} {count:5d}")

    print(f"\n📡 SESSION EVENTS: {len(events_df)}")
    print(f"   Avg events/txn: {len(events_df)/len(transactions_df):.1f}")
    print(f"   Step types: {events_df['step_name'].value_counts().to_dict()}")
    retry_count = events_df["is_retry"].sum()
    print(f"   Retries: {retry_count} ({retry_count/len(events_df)*100:.2f}%)")

    print(f"\n📊 HOLDOUT SPLIT:")
    h = split_metadata["holdout"]
    t = split_metadata["train"]
    print(f"   Train:   {t['n_mandates']} mandates, {t['n_transactions']} txns "
          f"({t['n_attacks']} attacks, {t['attack_rate']}%)")
    print(f"   Holdout: {h['n_mandates']} mandates, {h['n_transactions']} txns "
          f"({h['n_attacks']} attacks, {h['attack_rate']}%)")
    print(f"   ⚠️  WARNING: {split_metadata['WARNING']}")

    # Sample rows
    print(f"\n📝 SAMPLE MANDATE:")
    sample_mandate = mandates_df.iloc[0]
    for col in ["mandate_id", "payer_vpa", "amount_ceiling", "amount_rule",
                 "cadence", "purpose", "lifecycle_state"]:
        print(f"   {col:25s}: {sample_mandate[col]}")

    print(f"\n📝 SAMPLE LEGITIMATE TRANSACTION:")
    legit_sample = transactions_df[transactions_df["label"] == "legitimate"].iloc[0]
    for col in ["transaction_id", "mandate_id", "amount", "cart_category",
                 "agent_type", "label"]:
        print(f"   {col:25s}: {legit_sample[col]}")

    print(f"\n📝 SAMPLE ATTACK TRANSACTION (A6 — Injected Intent):")
    a6_samples = transactions_df[transactions_df["attack_class"] == "A6_injected_intent"]
    if len(a6_samples) > 0:
        a6_sample = a6_samples.iloc[0]
        for col in ["transaction_id", "mandate_id", "amount", "cart_items",
                     "cart_category", "cart_matches_purpose", "label", "attack_class"]:
            print(f"   {col:25s}: {a6_sample[col]}")

    print("\n" + "=" * 70)


def main():
    """Run the full simulation pipeline."""
    parser = argparse.ArgumentParser(description="ATRL Synthetic Data Simulator")
    parser.add_argument(
        "--output-dir", type=str, default="simulator/data",
        help="Directory to write output files",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--format", type=str, default="parquet",
        choices=["parquet", "sqlite"],
        help="Output format",
    )
    args = parser.parse_args()

    # Initialize logging
    setup_logging("INFO")

    # Build config
    config = SimulatorConfig(seed=args.seed)

    logger.info(
        "simulator_starting",
        version=config.version,
        seed=config.seed,
        n_mandates=config.mandate.count,
        n_transactions=config.transaction.target_count,
        attack_rate=config.transaction.attack_rate,
    )

    # ── Step 1: Generate mandates ────────────────────────────────
    logger.info("generating_mandates")
    mandates_df = generate_mandates(config)

    # ── Step 2: Generate transactions + session events ───────────
    logger.info("generating_transactions")
    transactions_df, events_df = generate_transactions(mandates_df, config)

    # ── Step 3: Write outputs ────────────────────────────────────
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.format == "parquet":
        mandates_out = _serialize_list_columns(mandates_df)
        txn_out = _serialize_list_columns(transactions_df)

        mandates_out.to_parquet(output_dir / "mandates.parquet", index=False)
        txn_out.to_parquet(output_dir / "transactions.parquet", index=False)
        events_df.to_parquet(output_dir / "session_events.parquet", index=False)

        logger.info(
            "data_written",
            format="parquet",
            mandates_path=str(output_dir / "mandates.parquet"),
            transactions_path=str(output_dir / "transactions.parquet"),
            events_path=str(output_dir / "session_events.parquet"),
        )
    else:
        raise NotImplementedError("SQLite output not yet implemented")

    # ── Step 4: Create holdout split ─────────────────────────────
    holdout_path = Path("results/holdout_split.json")
    split_metadata = create_holdout_split(
        mandates_df, transactions_df, config, holdout_path,
    )

    # ── Step 5: Write generation metadata ────────────────────────
    gen_metadata = {
        "version": config.version,
        "seed": config.seed,
        "generated_at": datetime.now(IST).isoformat(),
        "counts": {
            "mandates": len(mandates_df),
            "transactions": len(transactions_df),
            "session_events": len(events_df),
            "attacks": int((transactions_df["label"] == "attack").sum()),
            "hard_negatives": int(
                (transactions_df["hard_negative_type"] != "none").sum()
            ),
        },
        "attack_rate": round(
            (transactions_df["label"] == "attack").mean() * 100, 2
        ),
    }
    with open(output_dir / "generation_metadata.json", "w") as f:
        json.dump(gen_metadata, f, indent=2)

    # ── Step 6: Print stats ──────────────────────────────────────
    print_stats(mandates_df, transactions_df, events_df, split_metadata)

    logger.info("simulator_complete")


if __name__ == "__main__":
    main()
