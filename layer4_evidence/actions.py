"""Guarded post-decision actions. No action is attempted without required TPAP fields."""
from __future__ import annotations

from typing import Any
from layer4_evidence.store import EvidenceStore

def _audit(packet: dict, store: EvidenceStore | None, result: dict) -> dict:
    if store: store.save_action(packet["transaction"].get("transaction_id", "unknown"), result.get("action", "no_action"), result)
    return result


def apply_auto_response(packet: dict[str, Any], razorpay_client: Any | None, pause_payload: dict | None = None, store: EvidenceStore | None = None) -> dict:
    liability = packet["liability_determination"]
    mandate = packet["mandate_snapshot"]
    if packet["transaction_disposition"] == "pending re-authorization":
        result = {"status": "pending_reauthorization", "action": "step_up_requested", "reason": "medium behavioral risk; authority and intent remain coherent"}
        return _audit(packet, store, result)
    if liability not in {"fraud-contest", "escalate-to-provider"} or mandate.get("lifecycle_state") != "active":
        return _audit(packet, store, {"status": "no_action", "action": "none", "reason": "liability does not require pausing an active mandate"})
    if razorpay_client is None:
        return _audit(packet, store, {"status": "not_attempted", "action": "pause_mandate", "reason": "no Razorpay client configured"})
    if not pause_payload:
        return _audit(packet, store, {"status": "not_attempted", "action": "pause_mandate", "reason": "TPAP pause payload (UPI credentials/device fields) was not supplied"})
    try:
        result = razorpay_client.pause_mandate(mandate["mandate_id"], pause_payload, idempotency_key=f"pause-{packet['transaction'].get('transaction_id', 'unknown')}")
        return _audit(packet, store, {"status": "completed", "action": "pause_mandate", "response": result})
    except Exception as exc:
        return _audit(packet, store, {"status": "failed", "action": "pause_mandate", "error_type": type(exc).__name__, "error": str(exc)})
