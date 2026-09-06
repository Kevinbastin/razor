"""Layer 3 — explainable intent-integrity checks.

This module deliberately keeps the decision boundary small and inspectable:
I1 compares two text embeddings, while I4 requires all three independently
observable conditions.  It does not infer a user's intent from a black-box
risk score.
"""

from __future__ import annotations

import ast
import math
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Iterable

import numpy as np
import structlog
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from observability.metrics import METRICS
from layer3_intent.provenance import assess_provenance

logger = structlog.get_logger(__name__)


# These vocabulary expansions make the lightweight fallback a sentence-level
# embedding rather than a brittle literal word-overlap check.  They are shown
# in the implementation so an analyst can audit exactly why a match occurred.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "grocery": ("rice", "dal", "oil", "vegetables", "fruits", "milk", "bread", "eggs", "spices", "snacks", "grocery", "convenience"),
    "food": ("biryani", "pizza", "burger", "thali", "dosa", "noodles", "salad", "dessert", "juice", "coffee", "food", "delivery", "restaurant"),
    "household": ("detergent", "soap", "shampoo", "toothpaste", "tissue", "batteries", "bulbs", "cleaning", "household", "essentials"),
    "health": ("vitamins", "bandages", "sanitizer", "masks", "pain relief", "cough syrup", "thermometer", "pharmacy", "health"),
    "bakery": ("bread", "cake", "cookies", "pastry", "muffins", "biscuits", "chips", "namkeen", "bakery"),
    "luxury": ("gold", "diamond", "luxury", "designer", "watch", "handbag"),
    "electronics": ("laptop", "smartphone", "gaming", "console", "electronics"),
    "travel": ("airline", "ticket", "hotel", "booking", "travel"),
    "cash_like": ("cryptocurrency", "voucher", "gift card", "casino", "chips"),
}

INJECTION_MARKERS = ("ignore previous", "ignore all previous", "bypass confirmation", "do not ask", "system message", "agent instructions", "<!--", "opacity:0", "color:white", "transfer to", "upi id", "beneficiary")

def scan_injection_content(transaction: dict) -> dict[str, Any]:
    """Explainable structural scan for content the agent ingested before payment."""
    content = transaction.get("touched_content") or transaction.get("external_content") or []
    if isinstance(content, str): content = [content]
    hits = []
    for entry in content if isinstance(content, list) else []:
        text = str(entry).lower(); found = [marker for marker in INJECTION_MARKERS if marker in text]
        payload_like = "amount" in text and ("beneficiary" in text or "upi" in text)
        if found or payload_like: hits.append({"markers": found, "fully_specified_payment_payload": payload_like})
    return {"triggered": bool(hits), "matches": hits, "sources_checked": len(content) if isinstance(content, list) else 0}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value]
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except (ValueError, SyntaxError):
            pass
        return [value]
    return []


def cart_description(transaction: dict) -> str:
    """Stable natural-language representation of the purchased outcome."""
    items = ", ".join(_as_list(transaction.get("cart_items"))) or "unspecified items"
    category = str(transaction.get("cart_category") or "unspecified category").replace("_", " ")
    return f"Cart contents: {items}. Merchant category: {category}."


def _concept_words(text: str) -> str:
    lower = text.lower().replace("_", " ")
    hits = [name for name, words in CONCEPTS.items() if any(word in lower for word in words)]
    # Repeating concept tokens weights semantic category correspondence while
    # retaining original words for the TF-IDF representation.
    return " ".join(hits + hits)


class TextEmbedder:
    """Sentence embedder with an offline, deterministic fallback.

    The preferred backend is ``all-MiniLM-L6-v2`` when it is locally available.
    CI/demo environments need no model download: TF-IDF over the sentence plus
    auditable domain concepts remains a real vector embedding and cosine score.
    """

    def __init__(self, prefer_sentence_transformer: bool = False):
        self.backend = "tfidf_domain_sentence_embedding"
        self.model = None
        self.vectorizer: TfidfVectorizer | None = None
        if prefer_sentence_transformer:
            try:
                from sentence_transformers import SentenceTransformer
                self.model = SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
                self.backend = "sentence-transformers/all-MiniLM-L6-v2"
            except Exception:
                # Never silently download a model during payment evaluation.
                self.model = None

    @staticmethod
    def _prepare(text: str) -> str:
        return f"{text} {_concept_words(text)}"

    def fit(self, texts: Iterable[str]) -> "TextEmbedder":
        if self.model is None:
            self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), sublinear_tf=True)
            self.vectorizer.fit([self._prepare(t) for t in texts])
        return self

    def encode(self, texts: Iterable[str]) -> np.ndarray:
        prepared = [self._prepare(t) for t in texts]
        if self.model is not None:
            return np.asarray(self.model.encode(prepared, normalize_embeddings=True))
        if self.vectorizer is None:
            raise RuntimeError("embedder_not_fitted")
        return self.vectorizer.transform(prepared).toarray()


@dataclass
class IntentIntegrityResult:
    verdict: str
    signals_triggered: list[str]
    evidence: dict[str, dict[str, Any]]

    def to_dict(self) -> dict:
        return asdict(self)


class IntentIntegrityDetector:
    """Scores I1 and I4 using only prior transactions for each mandate."""

    def __init__(self, i1_threshold: float = 0.35, min_history: int = 8, prefer_sentence_transformer: bool = False):
        self.i1_threshold = float(i1_threshold)
        self.min_history = int(min_history)
        self.embedder = TextEmbedder(prefer_sentence_transformer=prefer_sentence_transformer)
        self._fitted = False

    def fit(self, mandates: Iterable[dict], transactions: Iterable[dict]) -> "IntentIntegrityDetector":
        texts: list[str] = []
        for mandate in mandates:
            texts.append(str(mandate.get("purpose") or ""))
        for txn in transactions:
            texts.append(cart_description(txn))
        self.embedder.fit(texts)
        self._fitted = True
        return self

    def score_transaction(self, transaction: dict, mandate: dict, prior_transactions: list[dict]) -> IntentIntegrityResult:
        """Return the Layer 3 contract. Missing semantic fields fail closed."""
        if not self._fitted:
            raise RuntimeError("intent_detector_not_fitted")
        purpose = mandate.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip() or not _as_list(transaction.get("cart_items")):
            output = IntentIntegrityResult(
                verdict="flagged", signals_triggered=["I1"],
                evidence={"I1": {"error": "missing purpose or cart contents", "fail_mode": "malformed_input", "threshold": self.i1_threshold}},
            )
            logger.warning("layer3_decision", transaction_id=transaction.get("transaction_id"), verdict=output.verdict, signals=output.signals_triggered, fail_mode="malformed_input")
            METRICS.record("layer3", output.verdict, actual_legitimate=transaction.get("label") == "legitimate" if "label" in transaction else None)
            return output

        purpose_vector, cart_vector = self.embedder.encode([purpose, cart_description(transaction)])
        similarity = float(cosine_similarity([purpose_vector], [cart_vector])[0][0])
        i1_triggered = similarity < self.i1_threshold

        i2 = assess_provenance(transaction, prior_transactions)
        i4 = self._i4_evidence(transaction, prior_transactions)
        i3 = scan_injection_content(transaction)
        signals = (["I1"] if i1_triggered else []) + (["I2"] if i2["triggered"] else []) + (["I3"] if i3["triggered"] else []) + (["I4"] if i4["triggered"] else [])
        output = IntentIntegrityResult(
            verdict="flagged" if signals else "clear",
            signals_triggered=signals,
            evidence={
                "I1": {
                    "similarity_score": round(similarity, 4),
                    "distance": round(1.0 - similarity, 4),
                    "threshold": self.i1_threshold,
                    "embedding_backend": self.embedder.backend,
                    "purpose": purpose,
                    "cart_description": cart_description(transaction),
                },
                "I4": i4,
                "I3": i3,
                "I2": i2,
            },
        )
        logger.info("layer3_decision", transaction_id=transaction.get("transaction_id"), mandate_id=transaction.get("mandate_id"), verdict=output.verdict, signals=output.signals_triggered)
        METRICS.record("layer3", output.verdict, actual_legitimate=transaction.get("label") == "legitimate" if "label" in transaction else None)
        return output

    def _i4_evidence(self, transaction: dict, history: list[dict]) -> dict[str, Any]:
        beneficiary = transaction.get("beneficiary_id")
        amount = float(transaction.get("amount", 0.0))
        seen = {h.get("beneficiary_id") for h in history}
        first_time = bool(beneficiary) and beneficiary not in seen
        amounts = [float(h["amount"]) for h in history if h.get("amount") is not None]
        quartile = float(np.percentile(amounts, 75)) if len(amounts) >= self.min_history else None
        upper_quartile = quartile is not None and amount >= quartile

        txn_hour = self._hour(transaction.get("timestamp"))
        hours = [self._hour(h.get("timestamp")) for h in history]
        hours = [h for h in hours if h is not None]
        timing_deviation, timing_distance, tolerance = self._timing_deviation(txn_hour, hours)
        return {
            "triggered": bool(first_time and upper_quartile and timing_deviation),
            "beneficiary_id": beneficiary,
            "first_time_beneficiary": first_time,
            "prior_transaction_count": len(history),
            "transaction_amount": amount,
            "upper_quartile_threshold": round(quartile, 2) if quartile is not None else None,
            "upper_quartile_value": upper_quartile,
            "transaction_hour": txn_hour,
            "timing_distance_hours": round(timing_distance, 2) if timing_distance is not None else None,
            "timing_tolerance_hours": round(tolerance, 2) if tolerance is not None else None,
            "timing_deviates_from_pattern": timing_deviation,
            "requires_all": ["first_time_beneficiary", "upper_quartile_value", "timing_deviates_from_pattern"],
        }

    @staticmethod
    def _hour(timestamp: Any) -> float | None:
        try:
            dt = datetime.fromisoformat(str(timestamp))
            return dt.hour + dt.minute / 60.0
        except (TypeError, ValueError):
            return None

    def _timing_deviation(self, txn_hour: float | None, hours: list[float]) -> tuple[bool, float | None, float | None]:
        if txn_hour is None or len(hours) < self.min_history:
            return False, None, None
        angles = np.asarray(hours) * 2 * math.pi / 24
        mean_angle = math.atan2(np.sin(angles).mean(), np.cos(angles).mean())
        if mean_angle < 0:
            mean_angle += 2 * math.pi
        centre = mean_angle * 24 / (2 * math.pi)
        distances = np.array([abs(((hour - centre + 12) % 24) - 12) for hour in hours])
        # Robust, understandable rule: outside the historical central range
        # plus 1 hour. It avoids marking a naturally broad schedule as off-pattern.
        tolerance = max(1.0, float(np.percentile(distances, 75)) + 1.0)
        distance = abs(((txn_hour - centre + 12) % 24) - 12)
        return bool(distance > tolerance), float(distance), tolerance
