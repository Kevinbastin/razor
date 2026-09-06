"""
Layer 2 — Behavioral Risk Feature Engineering.

Extracts session-level behavioral features from transaction and session-event
data. These features capture four signal families that help detect attacks
that pass Layer 1's deterministic checks (especially A1 consent replay,
A5 slow drain, and residual A2/A4 edge cases).

IMPORTANT: A6 (injected intent) is NOT expected to be caught here — that's
Layer 3's job. Layer 2 focuses on HOW the session behaves, not WHAT was bought.

Feature families:
    1. Sequence features  — API call ordering anomalies
    2. Timing features    — latency patterns (low variance = scripted replay)
    3. Credential features — token reuse, age, cross-device use
    4. Velocity/drift     — per-mandate personalized baselines, burst detection
"""

import json
from collections import Counter
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ── Canonical API call sequence ──────────────────────────────────────
# The "normal" flow an agent follows. Deviations from this are suspicious.
CANONICAL_SEQUENCE = [
    "discover_mandate",
    "validate_mandate",
    "build_cart",
    "compute_amount",
    "check_consent",
    "initiate_payment",
    "confirm_payment",
]

# Map step names to integers for n-gram encoding
STEP_TO_IDX = {step: i for i, step in enumerate(CANONICAL_SEQUENCE)}
# Add redirect_cart as an extra step (appears in A6 attacks ~30% of the time)
STEP_TO_IDX["redirect_cart"] = len(CANONICAL_SEQUENCE)


# ══════════════════════════════════════════════════════════════════════
# ══════════════════════════════════════════════════════════════════════
# FAMILY 1: SEQUENCE FEATURES
# ══════════════════════════════════════════════════════════════════════

def _extract_sequence_features(events: List[dict]) -> Dict[str, float]:
    """
    Extract features from the ordered API call sequence within a session.

    Captures:
        - N-gram profile: bigram and trigram distributions vs. canonical
        - Skip-ahead detection: payment without preceding cart/consent
        - Edit distance from canonical: how different this sequence is
        - Abandoned step ratio: fraction of steps that failed/were retried
    """
    if not events:
        return {
            "seq_length": 0,
            "seq_unique_steps": 0,
            "seq_has_cart": 0,
            "seq_has_consent": 0,
            "seq_has_payment": 0,
            "seq_skip_ahead": 1,          # Missing sequence = suspicious
            "seq_edit_distance": 7,        # Max distance = full canonical len
            "seq_edit_distance_norm": 1.0,
            "seq_abandoned_ratio": 0.0,
            "seq_retry_count": 0,
            "seq_bigram_novelty": 1.0,
            "seq_has_redirect": 0,
            "seq_repeated_steps": 0,
        }

    # Sort events by step_index
    sorted_events = sorted(events, key=lambda x: x.get("step_index", 0))
    steps = [e.get("step_name", "") for e in sorted_events]
    statuses = [e.get("status", "success") for e in sorted_events]
    is_retry = [bool(e.get("is_retry", False)) for e in sorted_events]

    unique_steps = set(steps)
    n = len(steps)

    # ── Skip-ahead detection ─────────────────────────────────────
    has_cart = "build_cart" in unique_steps
    has_consent = "check_consent" in unique_steps
    has_payment = "initiate_payment" in unique_steps or "confirm_payment" in unique_steps
    skip_ahead = int(has_payment and (not has_cart or not has_consent))

    # ── Edit distance from canonical sequence ────────────────────
    clean_steps = [s for s, r in zip(steps, is_retry) if not r]
    edit_dist = _levenshtein(clean_steps, CANONICAL_SEQUENCE)
    edit_dist_norm = edit_dist / max(len(CANONICAL_SEQUENCE), 1)

    # ── Abandoned step ratio ─────────────────────────────────────
    failed_count = sum(1 for s in statuses if s != "success")
    retry_count = sum(1 for r in is_retry if r)
    abandoned_ratio = failed_count / max(n, 1)

    # ── Bigram novelty ───────────────────────────────────────────
    canonical_bigrams = set()
    for i in range(len(CANONICAL_SEQUENCE) - 1):
        canonical_bigrams.add(
            (CANONICAL_SEQUENCE[i], CANONICAL_SEQUENCE[i + 1])
        )
    observed_bigrams = []
    for i in range(len(clean_steps) - 1):
        observed_bigrams.append((clean_steps[i], clean_steps[i + 1]))
    if observed_bigrams:
        novel_count = sum(1 for bg in observed_bigrams if bg not in canonical_bigrams)
        bigram_novelty = novel_count / len(observed_bigrams)
    else:
        bigram_novelty = 0.0

    # ── Additional flags ─────────────────────────────────────────
    has_redirect = int("redirect_cart" in unique_steps)

    repeated = sum(
        1 for i in range(1, len(steps)) if steps[i] == steps[i - 1]
        and not is_retry[i]
    )

    return {
        "seq_length": n,
        "seq_unique_steps": len(unique_steps),
        "seq_has_cart": int(has_cart),
        "seq_has_consent": int(has_consent),
        "seq_has_payment": int(has_payment),
        "seq_skip_ahead": skip_ahead,
        "seq_edit_distance": edit_dist,
        "seq_edit_distance_norm": round(edit_dist_norm, 4),
        "seq_abandoned_ratio": round(abandoned_ratio, 4),
        "seq_retry_count": retry_count,
        "seq_bigram_novelty": round(bigram_novelty, 4),
        "seq_has_redirect": has_redirect,
        "seq_repeated_steps": repeated,
    }


def _levenshtein(s1: list, s2: list) -> int:
    """Compute Levenshtein (edit) distance between two sequences."""
    m, n = len(s1), len(s2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost,
            )
    return dp[m][n]


# ══════════════════════════════════════════════════════════════════════
# FAMILY 2: TIMING FEATURES
# ══════════════════════════════════════════════════════════════════════

def _extract_timing_features(events: List[dict]) -> Dict[str, float]:
    """
    Extract latency/timing features from inter-step intervals.

    KEY INSIGHT: Low latency variance is MORE suspicious than high variance.
    Real agents have variable inference time (thinking, re-planning, retrying).
    Scripted replay attacks tend to have mechanically uniform timing.
    """
    if not events or len(events) < 2:
        return {
            "timing_latency_mean_ms": 0.0,
            "timing_latency_std_ms": 0.0,
            "timing_latency_min_ms": 0.0,
            "timing_latency_max_ms": 0.0,
            "timing_latency_cv": 0.0,
            "timing_session_duration_ms": 0.0,
            "timing_density": 0.0,
            "timing_low_variance_flag": 0,
            "timing_latency_p25_ms": 0.0,
            "timing_latency_p75_ms": 0.0,
            "timing_latency_iqr_ms": 0.0,
        }

    latencies = np.array([
        float(e.get("latency_ms", 0)) for e in events if float(e.get("latency_ms", 0)) > 0
    ])

    if len(latencies) < 2:
        val = float(latencies[0]) if len(latencies) == 1 else 0.0
        return {
            "timing_latency_mean_ms": val,
            "timing_latency_std_ms": 0.0,
            "timing_latency_min_ms": val,
            "timing_latency_max_ms": val,
            "timing_latency_cv": 0.0,
            "timing_session_duration_ms": val,
            "timing_density": 0.0,
            "timing_low_variance_flag": 0,
            "timing_latency_p25_ms": val,
            "timing_latency_p75_ms": val,
            "timing_latency_iqr_ms": 0.0,
        }

    mean_lat = float(np.mean(latencies))
    std_lat = float(np.std(latencies))
    min_lat = float(np.min(latencies))
    max_lat = float(np.max(latencies))
    p25 = float(np.percentile(latencies, 25))
    p75 = float(np.percentile(latencies, 75))
    iqr = p75 - p25

    cv = std_lat / mean_lat if mean_lat > 0 else 0.0
    session_duration = float(np.sum(latencies))
    density = len(latencies) / (session_duration / 1000) if session_duration > 0 else 0.0
    low_variance_flag = int(cv < 0.15 and mean_lat < 200)

    return {
        "timing_latency_mean_ms": round(mean_lat, 2),
        "timing_latency_std_ms": round(std_lat, 2),
        "timing_latency_min_ms": round(min_lat, 2),
        "timing_latency_max_ms": round(max_lat, 2),
        "timing_latency_cv": round(cv, 4),
        "timing_session_duration_ms": round(session_duration, 2),
        "timing_density": round(density, 4),
        "timing_low_variance_flag": low_variance_flag,
        "timing_latency_p25_ms": round(p25, 2),
        "timing_latency_p75_ms": round(p75, 2),
        "timing_latency_iqr_ms": round(iqr, 2),
    }


# ══════════════════════════════════════════════════════════════════════
# FAMILY 3: CREDENTIAL FEATURES
# ══════════════════════════════════════════════════════════════════════

def _extract_credential_features(
    events: List[dict],
    consent_token_history: Dict[str, List[dict]],
    txn_timestamp_epoch_ms: int,
) -> Dict[str, float]:
    """
    Extract credential/token-related features.
    """
    token_reuse_count = 0
    token_age_hours = 0.0
    has_consent_token = 0
    token_first_seen_gap_ms = 0

    consent_events = [e for e in events if e.get("step_name") == "check_consent"]

    if consent_events:
        token = consent_events[0].get("consent_token")
        if token and str(token) != "None" and not pd.isna(token):
            has_consent_token = 1
            token_str = str(token)
            prior_uses = consent_token_history.get(token_str, [])
            token_reuse_count = len(prior_uses)

            if prior_uses:
                first_use_ms = prior_uses[0].get("timestamp_epoch_ms", txn_timestamp_epoch_ms)
                gap_ms = txn_timestamp_epoch_ms - first_use_ms
                token_age_hours = max(gap_ms / (1000 * 3600), 0)
                token_first_seen_gap_ms = max(gap_ms, 0)

    return {
        "cred_has_consent_token": has_consent_token,
        "cred_token_reuse_count": token_reuse_count,
        "cred_token_reused": int(token_reuse_count > 0),
        "cred_token_age_hours": round(token_age_hours, 2),
        "cred_token_first_seen_gap_ms": token_first_seen_gap_ms,
    }


# ══════════════════════════════════════════════════════════════════════
# FAMILY 4: VELOCITY / DRIFT FEATURES
# ══════════════════════════════════════════════════════════════════════

def _extract_velocity_features(
    txn: dict,
    mandate_history: List[dict],
    mandate: dict,
) -> Dict[str, float]:
    """
    Extract velocity and per-mandate drift features.
    """
    if not mandate_history:
        return {
            "vel_mandate_txn_count": 0,
            "vel_txns_last_1h": 0,
            "vel_txns_last_24h": 0,
            "vel_txns_last_7d": 0,
            "vel_amount_z_score": 0.0,
            "vel_amount_vs_mean_ratio": 1.0,
            "vel_burst_score": 0.0,
            "vel_spend_velocity_pct": 0.0,
            "vel_is_first_txn": 1,
            "vel_time_since_last_hours": -1.0,
        }

    txn_epoch = txn.get("timestamp_epoch_ms", 0)
    txn_amount = float(txn.get("amount", 0))

    hist_amounts = [float(h.get("amount", 0)) for h in mandate_history]
    hist_epochs = [h.get("timestamp_epoch_ms", 0) for h in mandate_history]

    one_hour_ms = 3600 * 1000
    one_day_ms = 24 * one_hour_ms
    seven_days_ms = 7 * one_day_ms

    txns_1h = sum(1 for e in hist_epochs if txn_epoch - e < one_hour_ms and e < txn_epoch)
    txns_24h = sum(1 for e in hist_epochs if txn_epoch - e < one_day_ms and e < txn_epoch)
    txns_7d = sum(1 for e in hist_epochs if txn_epoch - e < seven_days_ms and e < txn_epoch)

    if len(hist_amounts) >= 3:
        hist_mean = float(np.mean(hist_amounts))
        hist_std = float(np.std(hist_amounts))
        z_score = (txn_amount - hist_mean) / hist_std if hist_std > 0 else 0.0
        amount_ratio = txn_amount / hist_mean if hist_mean > 0 else 1.0
    else:
        z_score = 0.0
        amount_ratio = 1.0

    cadence = mandate.get("cadence", "weekly")
    cadence_expected_per_day = {
        "daily": 1.0,
        "weekly": 1 / 7,
        "biweekly": 1 / 14,
        "monthly": 1 / 30,
        "on_demand": 2.0,
    }
    expected_daily = cadence_expected_per_day.get(cadence, 1 / 7)
    expected_hourly = expected_daily / 24
    burst_score = txns_1h / max(expected_hourly, 0.001) if txns_1h > 0 else 0.0

    cumulative = float(txn.get("cumulative_mandate_spend", 0))
    spend_limit = float(mandate.get("cumulative_spend_limit", 1))
    spend_velocity_pct = (cumulative / spend_limit * 100) if spend_limit > 0 else 0.0

    prior_epochs = [e for e in hist_epochs if e < txn_epoch]
    if prior_epochs:
        last_epoch = max(prior_epochs)
        time_since_last_hours = (txn_epoch - last_epoch) / (1000 * 3600)
    else:
        time_since_last_hours = -1.0

    return {
        "vel_mandate_txn_count": len(mandate_history),
        "vel_txns_last_1h": txns_1h,
        "vel_txns_last_24h": txns_24h,
        "vel_txns_last_7d": txns_7d,
        "vel_amount_z_score": round(float(z_score), 4),
        "vel_amount_vs_mean_ratio": round(float(amount_ratio), 4),
        "vel_burst_score": round(float(burst_score), 4),
        "vel_spend_velocity_pct": round(float(spend_velocity_pct), 2),
        "vel_is_first_txn": int(len(mandate_history) == 0),
        "vel_time_since_last_hours": round(float(time_since_last_hours), 2),
    }


# ══════════════════════════════════════════════════════════════════════
# TRANSACTION-LEVEL RAW FEATURES
# ══════════════════════════════════════════════════════════════════════

def _extract_transaction_features(txn: dict, mandate: dict) -> Dict[str, float]:
    """
    Extract basic transaction-level features used alongside behavioral features.
    """
    amount = float(txn.get("amount", 0))
    ceiling = float(mandate.get("amount_ceiling", 1))

    return {
        "txn_amount": amount,
        "txn_amount_ceiling_ratio": round(amount / ceiling, 4) if ceiling > 0 else 0.0,
        "txn_hour_of_day": _extract_hour(txn.get("timestamp", "")),
        "txn_is_weekend": _extract_is_weekend(txn.get("timestamp", "")),
        "txn_cumulative_spend_ratio": round(
            float(txn.get("cumulative_mandate_spend", 0)) / ceiling, 4
        ) if ceiling > 0 else 0.0,
    }


def _extract_hour(ts: str) -> int:
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts).hour
    except Exception:
        return 12


def _extract_is_weekend(ts: str) -> int:
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(ts).weekday() >= 5)
    except Exception:
        return 0


# ══════════════════════════════════════════════════════════════════════
# MAIN FEATURE EXTRACTION PIPELINE
# ══════════════════════════════════════════════════════════════════════

def extract_all_features(
    transactions_df: pd.DataFrame,
    session_events_df: pd.DataFrame,
    mandates_df: pd.DataFrame,
    mandate_ids_to_include: Optional[set] = None,
) -> pd.DataFrame:
    """
    Extract full feature vectors for a set of transactions.
    """
    from collections import defaultdict

    mandate_lookup = {}
    for row in mandates_df.to_dict("records"):
        for col in ("permitted_mccs", "permitted_categories", "beneficiaries"):
            val = row.get(col)
            if isinstance(val, str):
                try:
                    row[col] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    pass
        mandate_lookup[row["mandate_id"]] = row

    session_lookup = defaultdict(list)
    for ev in session_events_df.to_dict("records"):
        session_lookup[ev["session_id"]].append(ev)

    transactions_df = transactions_df.sort_values("timestamp").reset_index(drop=True)
    txn_records = transactions_df.to_dict("records")

    mandate_history: Dict[str, List[dict]] = {}
    consent_token_history: Dict[str, List[dict]] = {}
    mandate_agent_types: Dict[str, set] = {}

    feature_rows = []

    for txn_dict in txn_records:
        mandate_id = txn_dict.get("mandate_id")
        session_id = txn_dict.get("session_id")
        mandate = mandate_lookup.get(mandate_id, {})
        txn_epoch = txn_dict.get("timestamp_epoch_ms", 0)

        events = session_lookup.get(session_id, [])

        # Process features if this mandate is in the target set
        include_this = mandate_ids_to_include is None or (mandate_id in mandate_ids_to_include)

        seq_feats = _extract_sequence_features(events)
        timing_feats = _extract_timing_features(events)
        cred_feats = _extract_credential_features(
            events, consent_token_history, txn_epoch
        )
        vel_feats = _extract_velocity_features(
            txn_dict,
            mandate_history.get(mandate_id, []),
            mandate,
        )
        txn_feats = _extract_transaction_features(txn_dict, mandate)

        agent_type = txn_dict.get("agent_type", "")
        if mandate_id not in mandate_agent_types:
            mandate_agent_types[mandate_id] = set()
        mandate_agent_types[mandate_id].add(agent_type)
        cred_feats["cred_agent_type_diversity"] = len(mandate_agent_types[mandate_id])

        row = {
            "transaction_id": txn_dict.get("transaction_id"),
            "mandate_id": mandate_id,
            "label": txn_dict.get("label"),
            "attack_class": txn_dict.get("attack_class"),
            "hard_negative_type": txn_dict.get("hard_negative_type", "none"),
            **seq_feats,
            **timing_feats,
            **cred_feats,
            **vel_feats,
            **txn_feats,
        }
        if include_this:
            feature_rows.append(row)

        if mandate_id:
            if mandate_id not in mandate_history:
                mandate_history[mandate_id] = []
            mandate_history[mandate_id].append({
                "amount": txn_dict.get("amount"),
                "timestamp_epoch_ms": txn_epoch,
            })

        if events:
            for e in events:
                if e.get("step_name") == "check_consent":
                    token = e.get("consent_token")
                    if token and str(token) != "None" and not pd.isna(token):
                        token_str = str(token)
                        if token_str not in consent_token_history:
                            consent_token_history[token_str] = []
                        consent_token_history[token_str].append({
                            "session_id": session_id,
                            "timestamp_epoch_ms": txn_epoch,
                        })
                    break

    return pd.DataFrame(feature_rows)


# List of feature column names (excludes metadata columns)
FEATURE_COLUMNS = [
    # Sequence features
    "seq_length", "seq_unique_steps", "seq_has_cart", "seq_has_consent",
    "seq_has_payment", "seq_skip_ahead", "seq_edit_distance",
    "seq_edit_distance_norm", "seq_abandoned_ratio", "seq_retry_count",
    "seq_bigram_novelty", "seq_has_redirect", "seq_repeated_steps",
    # Timing features
    "timing_latency_mean_ms", "timing_latency_std_ms",
    "timing_latency_min_ms", "timing_latency_max_ms",
    "timing_latency_cv", "timing_session_duration_ms",
    "timing_density", "timing_low_variance_flag",
    "timing_latency_p25_ms", "timing_latency_p75_ms",
    "timing_latency_iqr_ms",
    # Credential features
    "cred_has_consent_token", "cred_token_reuse_count",
    "cred_token_reused", "cred_token_age_hours",
    "cred_token_first_seen_gap_ms", "cred_agent_type_diversity",
    # Velocity/drift features
    "vel_mandate_txn_count", "vel_txns_last_1h", "vel_txns_last_24h",
    "vel_txns_last_7d", "vel_amount_z_score", "vel_amount_vs_mean_ratio",
    "vel_burst_score", "vel_spend_velocity_pct", "vel_is_first_txn",
    "vel_time_since_last_hours",
    # Transaction-level context features
    "txn_amount", "txn_amount_ceiling_ratio",
    "txn_hour_of_day", "txn_is_weekend", "txn_cumulative_spend_ratio",
]
# NOTE: txn_is_new_beneficiary intentionally excluded — beneficiary novelty
# is Layer 3's responsibility (intent integrity / first-time beneficiary scoring).

METADATA_COLUMNS = [
    "transaction_id", "mandate_id", "label", "attack_class", "hard_negative_type",
]
