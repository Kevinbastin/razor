"""Layer 4: deterministic liability and a complete audit-ready evidence packet."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
import structlog
from observability.metrics import METRICS

logger = structlog.get_logger(__name__)


@dataclass
class EvidencePacket:
    packet_version: str
    generated_at: str
    transaction: dict[str, Any]
    mandate_snapshot: dict[str, Any]
    layer1: dict[str, Any]
    layer2: dict[str, Any]
    layer3: dict[str, Any]
    session_timeline: list[dict[str, Any]]
    fulfillment_evidence: list[dict[str, Any]]
    liability_determination: str
    liability_reason: str
    transaction_disposition: str
    auto_responder: dict[str, Any]
    narrative: dict[str, Any] | None = None
    def to_dict(self) -> dict[str, Any]: return asdict(self)


def determine_liability(layer1: dict, layer2: dict, layer3: dict) -> tuple[str, str]:
    """Fixed precedence makes the liability decision reproducible and auditable."""
    failed = set(layer1.get("failed_checks") or [])
    l2_evidence = layer2.get("evidence") or {}
    replay = bool(l2_evidence.get("features_summary", {}).get("cred_token_reused"))
    if "V6" in failed or replay:
        return "fraud-contest", "Credential attestation failed or a replay signal was present."
    if layer1.get("verdict") == "fail":
        return "merchant-should-accept", "The transaction breached deterministic mandate scope."
    if layer3.get("verdict") == "flagged":
        return "escalate-to-provider", "Authority was valid but Layer 3 found an intent-integrity concern."
    return "merchant-defensible", "The transaction remained in mandate scope and Layer 3 found intent coherent."


def determine_disposition(layer1: dict, layer2: dict, layer3: dict, liability: str) -> str:
    if liability in {"fraud-contest", "escalate-to-provider", "merchant-should-accept"}:
        return "blocked"
    if layer1.get("verdict") == "pass" and layer3.get("verdict") == "clear" and layer2.get("verdict") == "suspicious":
        return "pending re-authorization"
    return "approved"


class EvidenceAssembler:
    def assemble(self, *, transaction: dict, mandate_snapshot: dict, layer1: dict, layer2: dict,
                 layer3: dict, session_timeline: list[dict], fulfillment_evidence: list[dict] | None = None, auto_responder: dict | None = None) -> EvidencePacket:
        liability, reason = determine_liability(layer1, layer2, layer3)
        output = EvidencePacket(
            packet_version="1.0", generated_at=datetime.now(timezone.utc).isoformat(), transaction=transaction,
            mandate_snapshot=mandate_snapshot, layer1=layer1, layer2=layer2, layer3=layer3,
            session_timeline=sorted(session_timeline, key=lambda event: event.get("timestamp", "")),
            fulfillment_evidence=fulfillment_evidence or [],
            liability_determination=liability, liability_reason=reason,
            transaction_disposition=determine_disposition(layer1, layer2, layer3, liability),
            auto_responder=auto_responder or {"status": "not_evaluated"},
        )
        logger.info("layer4_decision", transaction_id=transaction.get("transaction_id"), liability=output.liability_determination, disposition=output.transaction_disposition)
        METRICS.record("layer4", output.liability_determination)
        return output
