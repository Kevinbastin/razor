#!/usr/bin/env python3
"""Fail a candidate Layer 2 model if frozen-holdout precision or recall falls."""
import argparse, json, sys
from pathlib import Path
import pandas as pd
from sklearn.metrics import precision_score, recall_score

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from layer2_detector.features import FEATURE_COLUMNS, extract_all_features
from layer2_detector.model import BehavioralRiskModel

def metrics(model_path, features):
    model = BehavioralRiskModel.load(model_path); y = (features.label == "attack").astype(int)
    pred = model.predict_proba(features[FEATURE_COLUMNS]) >= .5
    return {"precision": precision_score(y, pred, zero_division=0), "recall": recall_score(y, pred, zero_division=0)}
def main():
    p=argparse.ArgumentParser(); p.add_argument("candidate"); p.add_argument("--baseline", default="results/layer2_model.joblib"); p.add_argument("--data-dir", default="simulator/data"); p.add_argument("--split", default="results/holdout_split.json"); a=p.parse_args()
    split=json.loads(Path(a.split).read_text()); ids=set(split["holdout"]["mandate_ids"]); d=Path(a.data_dir)
    tx=pd.read_parquet(d/"transactions.parquet"); events=pd.read_parquet(d/"session_events.parquet"); mandates=pd.read_parquet(d/"mandates.parquet")
    # Mandates are disjoint across frozen splits; only a holdout mandate's
    # history is relevant to its features, so avoid a needless full-data pass.
    tx=tx[tx.mandate_id.isin(ids)]; events=events[events.session_id.isin(set(tx.session_id.dropna()))]
    features=extract_all_features(tx, events, mandates)
    baseline, candidate=metrics(a.baseline, features), metrics(a.candidate, features)
    print(json.dumps({"baseline":baseline,"candidate":candidate}, indent=2))
    if candidate["precision"] < baseline["precision"] or candidate["recall"] < baseline["recall"]: raise SystemExit("REGRESSION: candidate underperformed baseline on frozen holdout")
if __name__ == "__main__": main()
