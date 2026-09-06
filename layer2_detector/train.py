"""
Layer 2 — Model Training Script.

Trains the Behavioral Risk LightGBM model strictly on the TRAINING split,
guaranteeing zero leakage from the held-out evaluation set (Design Principle #4).

Usage:
    python -m layer2_detector.train [--data-dir simulator/data] [--output results/layer2_model.joblib]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Tuple

import numpy as np
import pandas as pd
import structlog

sys.path.insert(0, str(Path(__file__).parent.parent))

from logging_config import setup_logging
from layer2_detector.features import FEATURE_COLUMNS, extract_all_features
from layer2_detector.model import BehavioralRiskModel

logger = structlog.get_logger(__name__)


def load_datasets(data_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load Parquet tables from the data directory."""
    mandates_df = pd.read_parquet(data_dir / "mandates.parquet")
    transactions_df = pd.read_parquet(data_dir / "transactions.parquet")
    session_events_df = pd.read_parquet(data_dir / "session_events.parquet")
    return mandates_df, transactions_df, session_events_df


def load_train_mandates(split_path: Path) -> set:
    """Load train mandate IDs from the frozen split file."""
    if not split_path.exists():
        raise FileNotFoundError(f"Holdout split not found at {split_path}")

    with open(split_path, "r") as f:
        data = json.load(f)

    train_ids = set(data["train"]["mandate_ids"])
    holdout_ids = set(data["holdout"]["mandate_ids"])

    # Double check no overlap
    overlap = train_ids.intersection(holdout_ids)
    if overlap:
        raise ValueError(f"Data leak detected! Overlap between train and holdout: {overlap}")

    logger.info(
        "loaded_train_split",
        n_train_mandates=len(train_ids),
        n_holdout_mandates=len(holdout_ids),
    )
    return train_ids


def train_layer2(
    data_dir: str = "simulator/data",
    split_path: str = "results/holdout_split.json",
    output_model_path: str = "results/layer2_model.joblib",
) -> BehavioralRiskModel:
    """
    Extract features and train Layer 2 model strictly on training data.
    """
    setup_logging("INFO")
    logger.info("layer2_training_started", data_dir=data_dir, split_path=split_path)

    data_path = Path(data_dir)
    mandates_df, transactions_df, session_events_df = load_datasets(data_path)
    train_mandate_ids = load_train_mandates(Path(split_path))

    logger.info("extracting_training_features")
    train_feat_df = extract_all_features(
        transactions_df=transactions_df,
        session_events_df=session_events_df,
        mandates_df=mandates_df,
        mandate_ids_to_include=train_mandate_ids,
    )

    logger.info(
        "training_features_extracted",
        n_samples=len(train_feat_df),
        n_features=len(FEATURE_COLUMNS),
        n_attacks=int((train_feat_df["label"] == "attack").sum()),
        n_legitimate=int((train_feat_df["label"] == "legitimate").sum()),
    )

    # Prepare X, y
    X = train_feat_df[FEATURE_COLUMNS].copy()
    y = (train_feat_df["label"] == "attack").astype(int).values

    # NOTE: We train on the FULL training split (no internal validation split).
    # The frozen held-out evaluation set (results/holdout_split.json) provides
    # external validation. An internal 80/20 early-stopping split was tested
    # but caused severe underfitting due to the small attack class size (~3%).

    # Positive class weighting for imbalanced fraud classification
    scale_pos_weight = 15.0
    logger.info("training_hyperparameters", scale_pos_weight=scale_pos_weight, n_estimators=150, learning_rate=0.05)

    model = BehavioralRiskModel(
        n_estimators=150,
        learning_rate=0.05,
        max_depth=6,
        num_leaves=31,
        min_child_samples=20,
        scale_pos_weight=scale_pos_weight,
        random_state=42,
    )

    model.fit(X, y)
    metadata_path = data_path / "generation_metadata.json"
    generation_metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    snapshot_version = f"simulator-{generation_metadata.get('version', 'unknown')}-seed-{generation_metadata.get('seed', 'unknown')}"
    model.save(output_model_path, data_snapshot_version=snapshot_version)

    # Print top 10 feature importances
    imp_df = model.get_feature_importances()
    print("\n" + "=" * 60)
    print("  LAYER 2 MODEL TRAINED — TOP 10 FEATURE IMPORTANCES")
    print("=" * 60)
    for i, row in imp_df.head(10).iterrows():
        print(f"   {i+1:>2}. {row['feature']:<35} : {row['importance']:>5}")
    print("=" * 60 + "\n")

    return model


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Train Layer 2 Behavioral Risk Model")
    parser.add_argument("--data-dir", type=str, default="simulator/data", help="Data directory")
    parser.add_argument("--split-path", type=str, default="results/holdout_split.json", help="Split metadata path")
    parser.add_argument("--output", type=str, default="results/layer2_model.joblib", help="Output model path")
    args = parser.parse_args()

    train_layer2(args.data_dir, args.split_path, args.output)
