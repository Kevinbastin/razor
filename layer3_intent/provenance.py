"""I2: explainable source-provenance checks for agent sessions."""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlparse


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def assess_provenance(transaction: dict, prior_transactions: list[dict], *, immediate_window_seconds: int = 120,
                      low_reputation_threshold: float = 0.35) -> dict[str, Any]:
    """Flag a payment closely following a newly-seen or low-reputation domain.

    ``source_exposures`` is an append-only list of {url, timestamp,
    reputation?} captured by the browsing/retrieval adapter. Reputation is a
    normalized [0, 1] value supplied by a future domain-reputation provider;
    no reputation is invented here.
    """
    exposures = transaction.get("source_exposures", [])
    if not isinstance(exposures, list):
        return {"triggered": True, "error": "source_exposures must be a list", "fail_mode": "malformed_input"}
    known_domains = set()
    for prior in prior_transactions:
        for exposure in prior.get("source_exposures", []) if isinstance(prior.get("source_exposures", []), list) else []:
            domain = urlparse(str(exposure.get("url", ""))).hostname
            if domain: known_domains.add(domain.lower())
    payment_time = _timestamp(transaction.get("timestamp"))
    candidates = []
    for exposure in exposures:
        if not isinstance(exposure, dict):
            continue
        domain = urlparse(str(exposure.get("url", ""))).hostname
        observed = _timestamp(exposure.get("timestamp"))
        if not domain or payment_time is None or observed is None:
            continue
        delta = (payment_time - observed).total_seconds()
        if 0 <= delta <= immediate_window_seconds:
            reputation = exposure.get("reputation")
            low_reputation = isinstance(reputation, (int, float)) and float(reputation) < low_reputation_threshold
            newly_seen = domain.lower() not in known_domains
            candidates.append({"domain": domain.lower(), "seconds_before_payment": round(delta, 2), "newly_seen_for_mandate": newly_seen, "reputation": reputation, "low_reputation": low_reputation, "triggered": newly_seen or low_reputation})
    triggered = any(item["triggered"] for item in candidates)
    return {"triggered": triggered, "immediate_window_seconds": immediate_window_seconds, "low_reputation_threshold": low_reputation_threshold, "known_domains_before_session": sorted(known_domains), "immediate_exposures": candidates}
