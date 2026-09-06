"""Versioned request-level pipeline for real-time transaction ingestion."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from layer1_verifier.checks import verify_transaction
from layer2_detector.model import BehavioralRiskModel
from layer3_intent.detector import IntentIntegrityDetector
from layer4_evidence import EvidenceAssembler, EvidenceStore
from merchant_policy import MerchantPolicyStore

ROOT = Path(__file__).parent


def evaluate_transaction(payload: dict[str, Any], *, merchant_id: str | None = None) -> dict[str, Any]:
    """Evaluate supplied data without silently fabricating missing behavioural input."""
    merchant_id = merchant_id or os.getenv("MERCHANT_ID", "demo-merchant")
    txn, mandate = payload.get("transaction"), payload.get("mandate")
    if not isinstance(txn, dict) or not isinstance(mandate, dict):
        raise ValueError("transaction and mandate objects are required")
    prior_transactions = payload.get("prior_transactions", [])
    session_timeline = payload.get("session_events", [])
    if not isinstance(prior_transactions, list) or not isinstance(session_timeline, list):
        raise ValueError("prior_transactions and session_events must be arrays")
    policy = MerchantPolicyStore().get(merchant_id)
    layer1 = verify_transaction(
        txn, mandate,
        known_mandate_ids={mandate.get("mandate_id")} if mandate.get("mandate_id") else None,
        key_registry=payload.get("key_registry"),
        recent_txn_timestamps=[str(item.get("timestamp")) for item in prior_transactions],
    ).to_dict()
    features = payload.get("layer2_features")
    if not isinstance(features, dict):
        # A missing behavioural feature vector is a fail-closed operational error.
        layer2 = {"verdict": "attack", "risk_score": 1.0, "top_risk_factors": [{"feature": "input", "reason": "missing_layer2_features"}], "evidence": {"error": "missing_layer2_features"}}
    else:
        model = BehavioralRiskModel.load(ROOT / "results/layer2_model.joblib")
        layer2 = model.score_single(features, threshold=policy["layer2_attack_threshold"], suspicious_threshold=policy["layer2_suspicious_threshold"]).to_dict()
    detector = IntentIntegrityDetector(i1_threshold=policy["i1_threshold"]).fit([mandate], [*prior_transactions, txn])
    layer3 = detector.score_transaction(txn, mandate, prior_transactions).to_dict()
    packet = EvidenceAssembler().assemble(transaction=txn, mandate_snapshot=layer1["mandate_snapshot"], layer1=layer1, layer2=layer2, layer3=layer3, session_timeline=session_timeline, fulfillment_evidence=payload.get("fulfillment_evidence") or [])
    result = packet.to_dict()
    result["merchant_id"] = merchant_id
    result["policy"] = policy
    EvidenceStore(ROOT / "results/evidence_audit.sqlite", merchant_id=merchant_id).save_packet(result)
    return result
