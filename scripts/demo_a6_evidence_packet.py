#!/usr/bin/env python3
"""Full synthetic A6 walkthrough across all four layers (no external calls required)."""
import json, sys
from collections import defaultdict
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from layer1_verifier.checks import verify_transaction
from layer2_detector.features import extract_all_features
from layer2_detector.model import BehavioralRiskModel
from layer3_intent.detector import IntentIntegrityDetector
from layer4_evidence.actions import apply_auto_response
from layer4_evidence.assembler import EvidenceAssembler
from layer4_evidence.narrative import generate_narrative
from layer4_evidence.store import EvidenceStore

def main():
    data = ROOT / "simulator/data"; mandates = pd.read_parquet(data / "mandates.parquet")
    txns = pd.read_parquet(data / "transactions.parquet").sort_values("timestamp"); events = pd.read_parquet(data / "session_events.parquet")
    split = json.loads((ROOT / "results/holdout_split.json").read_text()); holdout_ids = set(split["holdout"]["mandate_ids"])
    holdout = txns[txns.mandate_id.isin(holdout_ids)].copy(); train = txns[~txns.mandate_id.isin(holdout_ids)]
    mandates_by_id = {row["mandate_id"]: row for row in mandates.to_dict("records")}; known = set(mandates_by_id); keys = {key: row["primary_agent_type"] for key, row in mandates_by_id.items()}
    detector = IntentIntegrityDetector().fit(mandates[~mandates.mandate_id.isin(holdout_ids)].to_dict("records"), train.to_dict("records"))
    detector.i1_threshold = float(json.loads((ROOT / "results/layer3_evaluation.json").read_text())["i1_threshold"])
    session_ids = set(holdout.session_id.dropna()); feature_rows = extract_all_features(holdout, events[events.session_id.isin(session_ids)], mandates)
    features = {row["transaction_id"]: row for row in feature_rows.to_dict("records")}; l2model = BehavioralRiskModel.load(ROOT / "results/layer2_model.joblib")
    l1history, l3history = defaultdict(list), defaultdict(list)
    selected = None
    for txn in holdout.to_dict("records"):
        mid = txn["mandate_id"]
        if txn["attack_class"] == "A6_injected_intent":
            l1 = verify_transaction(txn, mandates_by_id[mid], known, keys, l1history[mid][-10:]); l2 = l2model.score_single(features[txn["transaction_id"]]); l3 = detector.score_transaction(txn, mandates_by_id[mid], l3history[mid])
            if l1.verdict == "pass" and l2.verdict == "pass" and l3.verdict == "flagged": selected = (txn, l1.to_dict(), l2.to_dict(), l3.to_dict()); break
        l1history[mid].append(txn["timestamp"]); l3history[mid].append(txn)
    if not selected: raise RuntimeError("No suitable frozen-holdout A6 demonstration row found")
    txn, l1, l2, l3 = selected; timeline = events[events.session_id == txn["session_id"]].to_dict("records")
    assembler = EvidenceAssembler(); packet = assembler.assemble(transaction=txn, mandate_snapshot=mandates_by_id[txn["mandate_id"]], layer1=l1, layer2=l2, layer3=l3, session_timeline=timeline, fulfillment_evidence=[{"type":"simulated_order_record", "reference":txn["transaction_id"], "status":"not_fulfilled_due_to_risk_hold"}])
    store = EvidenceStore(ROOT / "results/evidence_audit.sqlite"); store.save_packet(packet.to_dict())
    packet.auto_responder = apply_auto_response(packet.to_dict(), razorpay_client=None, store=store)
    packet.narrative = generate_narrative(packet.to_dict())
    output = ROOT / "results/a6_evidence_packet.json"; output.write_text(json.dumps(packet.to_dict(), indent=2, default=str) + "\n")
    print("\n" + "=" * 88); print(" A6 → LAYER 4 EVIDENCE PACKET (full worked example)"); print("=" * 88)
    print(f"Transaction: {txn['transaction_id']} | Cart: {txn['cart_items']}")
    print(f"Layer 1: {l1['verdict'].upper()} — mandate authority is intact")
    print(f"Layer 2: {l2['verdict'].upper()} — behavioral score {l2['risk_score']}")
    print(f"Layer 3: {l3['verdict'].upper()} — signals {', '.join(l3['signals_triggered'])}")
    print(f"Liability: {packet.liability_determination} — {packet.liability_reason}")
    print(f"Action: {packet.auto_responder['status']} ({packet.auto_responder.get('reason', '')})")
    print(f"Narrative: {packet.narrative['status']} (exact prompt retained in packet)")
    print(f"Packet written: {output}"); print("=" * 88 + "\n")

if __name__ == "__main__": main()
