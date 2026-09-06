"""
Agent Transaction Risk Layer — Layer 4: Evidence Generator

Assembles structured dispute-evidence packets from all three
layers' outputs. Submits via Razorpay's contest-dispute endpoint.

Produces liability determinations:
- merchant-defensible
- merchant-should-accept
- escalate-to-provider
- fraud
"""
from layer4_evidence.assembler import EvidenceAssembler, EvidencePacket, determine_liability
from layer4_evidence.store import EvidenceStore
from layer4_evidence.notifications import queue_risk_notification

__all__ = ["EvidenceAssembler", "EvidencePacket", "EvidenceStore", "queue_risk_notification", "determine_liability"]
