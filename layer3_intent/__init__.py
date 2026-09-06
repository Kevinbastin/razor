"""
Agent Transaction Risk Layer — Layer 3: Intent Integrity

The novel core. Detects hijacked payment goals even when the
transaction is technically valid and in-scope:
- Semantic divergence (mandate purpose vs. actual cart)
- First-time-beneficiary + high-value + off-pattern combos
- Content-source provenance analysis
"""

from layer3_intent.detector import IntentIntegrityDetector, IntentIntegrityResult, cart_description

__all__ = ["IntentIntegrityDetector", "IntentIntegrityResult", "cart_description"]
