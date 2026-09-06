"""Auditable notification payloads; delivery providers can be plugged in later."""
from __future__ import annotations
from layer4_evidence.store import EvidenceStore

def queue_risk_notification(packet: dict, store: EvidenceStore, *, recipient: str = "mandate_holder", channel: str = "in_app") -> dict:
    txn = packet["transaction"]
    signals = ", ".join(packet.get("layer3", {}).get("signals_triggered", [])) or "risk signals"
    message = f"Transaction {txn.get('transaction_id', 'unknown')} is {packet.get('transaction_disposition', 'held')}: {packet.get('liability_reason', 'review required')}. Signals: {signals}."
    store.save_notification(txn.get("transaction_id", "unknown"), recipient, channel, message)
    return {"status": "queued", "channel": channel, "recipient": recipient, "message": message}
