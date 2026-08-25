"""
Transaction & session-event generator.

Generates ~50,000 transactions against the mandate pool with:
- Realistic agent behavioral profiles (latency, sequence shape)
- Weekday/weekend and time-of-day rhythms
- 6 attack classes (~2-4% of total)
- Hard negatives (~4% of legitimate) to stress false-positive rate
- Step-by-step API call sequences with timestamps for Layer 2

Attack Classes:
    A1 — Consent-token replay: reuses a previous session's consent token
    A2 — Spoofed agent identity: mismatched signing key / agent ID
    A3 — Over-ceiling burst: amount exceeds mandate ceiling
    A4 — Off-window / revoked: transaction outside time window or on revoked mandate
    A5 — Slow drain: many small in-limit charges accumulating past cumulative limit
    A6 — Injected intent: valid transaction but cart/beneficiary don't match purpose
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import structlog

from simulator.config import SimulatorConfig

logger = structlog.get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))


def _weighted_choice(rng: np.random.Generator, items: list, weights: list, size: int = 1):
    """Weighted random selection."""
    probs = np.array(weights, dtype=float)
    probs /= probs.sum()
    indices = rng.choice(len(items), size=size, p=probs)
    if size == 1:
        return items[indices[0]]
    return [items[i] for i in indices]


# ── Time-of-day and day-of-week distributions ────────────────────────

# Hourly weights (quick-commerce peaks: lunch 11-14, dinner 18-21)
HOUR_WEIGHTS = [
    0.005, 0.002, 0.001, 0.001, 0.002, 0.005,  # 0-5: late night
    0.015, 0.030, 0.050, 0.065, 0.075, 0.085,   # 6-11: morning ramp
    0.090, 0.085, 0.060, 0.045, 0.050, 0.070,   # 12-17: afternoon
    0.085, 0.090, 0.080, 0.055, 0.035, 0.020,   # 18-23: evening peak
]

# Day-of-week weights (0=Mon, 6=Sun; weekends slightly higher)
DOW_WEIGHTS = [0.13, 0.13, 0.13, 0.14, 0.14, 0.16, 0.17]


def _pick_transaction_time(
    rng: np.random.Generator,
    mandate_granted: datetime,
    now: datetime,
) -> datetime:
    """Pick a realistic transaction timestamp respecting time-of-day rhythm."""
    # Pick a random date between granted and now
    span_days = max((now - mandate_granted).days, 1)
    day_offset = int(rng.integers(0, span_days))
    base_date = mandate_granted + timedelta(days=day_offset)

    # Weight by day-of-week
    dow = base_date.weekday()
    # Accept/reject based on DOW weight (simple rejection sampling)
    if rng.random() > DOW_WEIGHTS[dow] / max(DOW_WEIGHTS):
        # Re-roll once
        day_offset = int(rng.integers(0, span_days))
        base_date = mandate_granted + timedelta(days=day_offset)

    # Pick hour weighted by time-of-day distribution
    hour = int(rng.choice(24, p=np.array(HOUR_WEIGHTS) / sum(HOUR_WEIGHTS)))
    minute = int(rng.integers(0, 60))
    second = int(rng.integers(0, 60))

    return base_date.replace(
        hour=hour, minute=minute, second=second,
        tzinfo=IST,
    )


# ── Session event generation ─────────────────────────────────────────

def _generate_session_events(
    rng: np.random.Generator,
    txn_id: str,
    mandate_id: str,
    txn_time: datetime,
    agent_profile: dict,
    is_attack: bool,
    attack_class: str,
    include_retry: bool = False,
) -> List[dict]:
    """
    Generate step-by-step API call sequence for a transaction session.

    Standard flow: discover -> validate -> cart -> amount -> consent -> pay -> confirm
    Attack classes modify the sequence shape and timing.
    """
    steps = [
        "discover_mandate",
        "validate_mandate",
        "build_cart",
        "compute_amount",
        "check_consent",
        "initiate_payment",
        "confirm_payment",
    ]

    # Agent profile determines latency and optional extra steps
    latency_mean = agent_profile["latency_mean_ms"]
    latency_std = agent_profile["latency_std_ms"]
    step_min, step_max = agent_profile["step_count_range"]

    events = []
    current_time = txn_time
    consent_token = hashlib.sha256(f"{txn_id}_{mandate_id}".encode()).hexdigest()[:16]
    session_id = f"SES_{uuid.uuid4().hex[:12].upper()}"

    # Decide how many steps (some agents skip optional steps)
    n_steps = int(rng.integers(step_min, step_max + 1))
    n_steps = min(n_steps, len(steps))

    # Always include required steps, optionally include others
    required = {"discover_mandate", "validate_mandate", "initiate_payment", "confirm_payment"}
    optional = [s for s in steps if s not in required]
    selected_optional = list(rng.choice(
        optional, size=min(n_steps - len(required), len(optional)), replace=False
    )) if n_steps > len(required) else []
    active_steps = sorted(
        list(required) + selected_optional,
        key=lambda s: steps.index(s),
    )

    # Attack-specific sequence mutations
    if is_attack:
        if attack_class == "A1_consent_replay":
            # Reuse a consent token from a "previous" session
            consent_token = f"REPLAY_{consent_token[:10]}"
            # Slightly faster — skips validation sometimes
            if "validate_mandate" in active_steps and rng.random() < 0.3:
                pass  # keep it but timing will be abnormal
        elif attack_class == "A2_spoofed_identity":
            # Different timing signature — usually faster, mechanical
            latency_mean = max(50, latency_mean * 0.3)
            latency_std = max(10, latency_std * 0.2)
        elif attack_class == "A5_slow_drain":
            # Looks very normal — keep standard timing
            pass
        elif attack_class == "A6_injected_intent":
            # Mostly normal flow — the attack is in the payload, not the sequence
            # But might have a subtle extra step (e.g., redirect)
            if rng.random() < 0.3:
                active_steps.insert(-1, "redirect_cart")

    for i, step in enumerate(active_steps):
        # Compute inter-step latency
        latency_ms = max(10, rng.normal(latency_mean, latency_std))

        # Add some jitter for network effects
        if rng.random() < 0.05:
            latency_ms *= rng.uniform(2.0, 5.0)  # network hiccup

        step_time = current_time + timedelta(milliseconds=latency_ms)
        status = "success"

        # Simulate occasional failures with retry
        if include_retry and step in ("initiate_payment", "confirm_payment"):
            if rng.random() < agent_profile.get("retry_prob", 0.05):
                # Failed attempt
                events.append({
                    "session_id": session_id,
                    "transaction_id": txn_id,
                    "mandate_id": mandate_id,
                    "step_index": len(events),
                    "step_name": step,
                    "timestamp": step_time.isoformat(),
                    "timestamp_epoch_ms": int(step_time.timestamp() * 1000),
                    "latency_ms": round(latency_ms, 1),
                    "status": "failed_network",
                    "consent_token": consent_token if step == "check_consent" else None,
                    "agent_type": agent_profile["type"],
                    "is_retry": False,
                })
                # Retry after backoff
                backoff_ms = rng.uniform(500, 3000)
                step_time = step_time + timedelta(milliseconds=backoff_ms)
                latency_ms = backoff_ms
                status = "success"
                events.append({
                    "session_id": session_id,
                    "transaction_id": txn_id,
                    "mandate_id": mandate_id,
                    "step_index": len(events),
                    "step_name": step,
                    "timestamp": step_time.isoformat(),
                    "timestamp_epoch_ms": int(step_time.timestamp() * 1000),
                    "latency_ms": round(latency_ms, 1),
                    "status": status,
                    "consent_token": consent_token if step == "check_consent" else None,
                    "agent_type": agent_profile["type"],
                    "is_retry": True,
                })
                current_time = step_time
                continue

        events.append({
            "session_id": session_id,
            "transaction_id": txn_id,
            "mandate_id": mandate_id,
            "step_index": len(events),
            "step_name": step,
            "timestamp": step_time.isoformat(),
            "timestamp_epoch_ms": int(step_time.timestamp() * 1000),
            "latency_ms": round(latency_ms, 1),
            "status": status,
            "consent_token": consent_token if step == "check_consent" else None,
            "agent_type": agent_profile["type"],
            "is_retry": False,
        })
        current_time = step_time

    return events


# ── Cart / beneficiary generation ────────────────────────────────────

def _generate_cart(
    rng: np.random.Generator,
    mandate: dict,
    amount: float,
    merchant_config,
    is_a6_attack: bool = False,
) -> dict:
    """Generate cart contents matching (or deliberately mismatching) the mandate purpose."""
    purpose_idx = mandate["purpose_template_idx"]
    purpose_template = merchant_config.purpose_templates[purpose_idx]

    if is_a6_attack:
        # Injected intent — cart items don't match purpose, BUT the MCC/category
        # IS within the mandate's permitted scope. This is what makes A6 invisible
        # to Layer 1 and catchable ONLY by Layer 3 (intent integrity).
        # The attack hijacked the agent's goal, not its authority.
        off_items = list(rng.choice(
            merchant_config.off_purpose_items,
            size=int(rng.integers(1, 4)),
            replace=False,
        ))

        # Use a permitted category from the mandate (so V2 passes)
        permitted_cats = mandate.get("permitted_categories")
        if isinstance(permitted_cats, str):
            try:
                import json as _json
                permitted_cats = _json.loads(permitted_cats)
            except Exception:
                permitted_cats = None
        if permitted_cats and len(permitted_cats) > 0:
            cat_name = rng.choice(permitted_cats)
        else:
            # Fallback to purpose template category
            cat_name = rng.choice(purpose_template["categories"])

        # Find MCC for the chosen (permitted) category
        mcc = "5411"  # default grocery
        for c in merchant_config.permitted_categories:
            if c["name"] == cat_name:
                mcc = c["mcc"]
                break

        return {
            "items": off_items,
            "category": cat_name,       # CORRECT category (passes V2)
            "mcc": mcc,                 # CORRECT MCC (passes V2)
            "matches_purpose": False,   # But items don't match (Layer 3 signal)
        }
    else:
        # Legitimate cart
        n_items = int(rng.integers(1, 6))
        items = list(rng.choice(
            purpose_template["typical_items"],
            size=min(n_items, len(purpose_template["typical_items"])),
            replace=False,
        ))
        cat_name = rng.choice(purpose_template["categories"])
        # Find MCC for the category
        mcc = "5411"  # default grocery
        for c in merchant_config.permitted_categories:
            if c["name"] == cat_name:
                mcc = c["mcc"]
                break
        return {
            "items": items,
            "category": cat_name,
            "mcc": mcc,
            "matches_purpose": True,
        }


def _pick_beneficiary(
    rng: np.random.Generator,
    mandate: dict,
    is_a6_attack: bool = False,
) -> Tuple[str, bool]:
    """Pick a beneficiary. A6 attacks use first-time/suspicious beneficiaries."""
    if is_a6_attack and rng.random() < 0.7:
        # New suspicious beneficiary never seen before
        ben_id = f"SUS_{uuid.uuid4().hex[:8].upper()}"
        return ben_id, True
    else:
        # Known beneficiary
        beneficiaries = mandate["beneficiaries"]
        ben_id = rng.choice(beneficiaries)
        return ben_id, False


# ── Attack generators ────────────────────────────────────────────────

def _generate_a1_consent_replay(
    rng: np.random.Generator,
    mandate: dict,
    txn_time: datetime,
    config: SimulatorConfig,
) -> dict:
    """A1: Consent-token replay — reuses token from a previous session."""
    amount = float(rng.integers(
        max(50, int(mandate["amount_ceiling"] * 0.3)),
        int(mandate["amount_ceiling"] * 0.9) + 1,
    ))
    return {
        "amount": amount,
        "consent_token_reused": True,
        "original_token_age_hours": float(rng.integers(1, 72)),
    }


def _generate_a2_spoofed_identity(
    rng: np.random.Generator,
    mandate: dict,
    txn_time: datetime,
    config: SimulatorConfig,
) -> dict:
    """A2: Spoofed agent identity — mismatched signing key."""
    amount = float(rng.integers(
        max(50, int(mandate["amount_ceiling"] * 0.5)),
        mandate["amount_ceiling"] + 1,
    ))
    return {
        "amount": amount,
        "agent_id_mismatch": True,
        "spoofed_agent_type": rng.choice(["unknown_bot", "cloned_agent", "mitm_proxy"]),
    }


def _generate_a3_over_ceiling(
    rng: np.random.Generator,
    mandate: dict,
    txn_time: datetime,
    config: SimulatorConfig,
) -> dict:
    """A3: Over-ceiling burst — amount exceeds mandate ceiling."""
    # Exceed by 10-300%
    multiplier = rng.uniform(1.1, 4.0)
    amount = round(mandate["amount_ceiling"] * multiplier, 2)
    return {
        "amount": amount,
        "ceiling_exceeded_by": round(amount - mandate["amount_ceiling"], 2),
    }


def _generate_a4_off_window_revoked(
    rng: np.random.Generator,
    mandate: dict,
    txn_time: datetime,
    config: SimulatorConfig,
) -> dict:
    """A4: Off-window or revoked-mandate use."""
    amount = float(rng.integers(
        max(50, int(mandate["amount_ceiling"] * 0.3)),
        mandate["amount_ceiling"] + 1,
    ))
    violation_type = rng.choice(["off_window", "revoked_mandate", "expired_mandate"])
    return {
        "amount": amount,
        "violation_type": violation_type,
        "force_off_window": violation_type == "off_window",
        "force_revoked": violation_type in ("revoked_mandate", "expired_mandate"),
    }


def _generate_a5_slow_drain(
    rng: np.random.Generator,
    mandate: dict,
    txn_time: datetime,
    config: SimulatorConfig,
) -> dict:
    """A5: Slow drain — many small in-limit charges."""
    # Small amount that's well within ceiling
    amount = float(rng.integers(
        max(10, int(mandate["amount_ceiling"] * 0.05)),
        max(20, int(mandate["amount_ceiling"] * 0.25)) + 1,
    ))
    return {
        "amount": amount,
        "is_drain_charge": True,
        "drain_sequence_position": int(rng.integers(1, 30)),
    }


def _generate_a6_injected_intent(
    rng: np.random.Generator,
    mandate: dict,
    txn_time: datetime,
    config: SimulatorConfig,
) -> dict:
    """
    A6: Injected intent — valid credentials, active mandate, in-limit amount,
    but the cart/beneficiary don't match the stated purpose.
    This simulates a prompt-injection that hijacked the agent's payment goal.
    """
    # Amount is deliberately within limits to pass Layer 1
    if mandate.get("amount_rule") == "exact":
        amount = float(mandate["amount_ceiling"])
    else:
        amount = float(rng.integers(
            max(50, int(mandate["amount_ceiling"] * 0.4)),
            mandate["amount_ceiling"] + 1,
        ))
    return {
        "amount": amount,
        "intent_hijacked": True,
        "cart_mismatch": True,
    }


# ── Hard negative generators ────────────────────────────────────────

def _generate_hard_negative(
    rng: np.random.Generator,
    hn_type: str,
    mandate: dict,
    config: SimulatorConfig,
) -> dict:
    """Generate a legitimate transaction that superficially resembles an attack."""
    if mandate.get("amount_rule") == "exact":
        amount = float(mandate["amount_ceiling"])
    elif hn_type == "bulk_festival_order":
        # High amount near ceiling — looks like A3 but legitimate
        amount = float(rng.integers(
            int(mandate["amount_ceiling"] * 0.85),
            mandate["amount_ceiling"] + 1,
        ))
    elif hn_type == "first_time_beneficiary":
        # New beneficiary — looks like A6 but legitimate
        amount = float(rng.integers(
            max(50, int(mandate["amount_ceiling"] * 0.3)),
            int(mandate["amount_ceiling"] * 0.7) + 1,
        ))
    elif hn_type == "late_night_order":
        # Edge of time window — looks like A4 but within bounds
        amount = float(rng.integers(
            max(50, int(mandate["amount_ceiling"] * 0.2)),
            int(mandate["amount_ceiling"] * 0.6) + 1,
        ))
    elif hn_type == "rapid_retry_after_fail":
        # Quick succession — looks like A1 replay but genuine retry
        amount = float(rng.integers(
            max(50, int(mandate["amount_ceiling"] * 0.3)),
            int(mandate["amount_ceiling"] * 0.8) + 1,
        ))
    elif hn_type == "high_value_single":
        # Near ceiling single purchase — looks like A3 but under
        amount = float(rng.integers(
            int(mandate["amount_ceiling"] * 0.90),
            mandate["amount_ceiling"] + 1,
        ))
    else:
        amount = float(rng.integers(
            max(50, int(mandate["amount_ceiling"] * 0.3)),
            int(mandate["amount_ceiling"] * 0.7) + 1,
        ))

    flags = {}
    if hn_type == "late_night_order":
        flags["force_edge_hour"] = True
    elif hn_type == "rapid_retry_after_fail":
        flags["force_retry"] = True

    return {"amount": amount, "hn_reason": hn_type, **flags}


# ── Main transaction generator ───────────────────────────────────────

def generate_transactions(
    mandates_df: pd.DataFrame,
    config: SimulatorConfig,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Generate ~50,000 transactions with session events.

    Returns:
        (transactions_df, session_events_df)
    """
    rng = np.random.default_rng(config.seed + 1)  # Offset seed from mandates
    tc = config.transaction
    mc = config.merchant

    now = datetime(2025, 8, 25, 12, 0, 0, tzinfo=IST)

    # Only generate legitimate transactions against active mandates
    # (paused/revoked/expired mandates only get attack transactions like A4)
    active_mandates = mandates_df[
        mandates_df["lifecycle_state"] == "active"
    ].to_dict("records")
    all_mandates = mandates_df.to_dict("records")

    if not active_mandates:
        raise ValueError("No active mandates to generate transactions against")

    # Calculate target counts
    n_total = tc.target_count
    n_attacks = int(n_total * tc.attack_rate)
    n_legitimate = n_total - n_attacks
    n_hard_negatives = int(n_legitimate * tc.hard_negative_rate)
    n_normal = n_legitimate - n_hard_negatives

    logger.info(
        "transaction_generation_plan",
        total=n_total,
        attacks=n_attacks,
        hard_negatives=n_hard_negatives,
        normal_legitimate=n_normal,
    )

    transactions = []
    all_session_events = []

    # Cumulative spend tracker per mandate (for A5 slow drain)
    cumulative_spend: Dict[str, float] = {}

    # ── Generate normal legitimate transactions ──────────────────
    for i in range(n_normal):
        mandate = rng.choice(active_mandates)
        mandate_id = mandate["mandate_id"]
        txn_id = f"TXN_{uuid.uuid4().hex[:12].upper()}"

        granted_at = datetime.fromisoformat(mandate["granted_at"])
        txn_time = _pick_transaction_time(rng, granted_at, now)

        # Ensure within time window
        tw_start = mandate["time_window_start_hour"]
        tw_end = mandate["time_window_end_hour"]
        if tw_end > tw_start:
            txn_time = txn_time.replace(
                hour=int(rng.integers(tw_start, tw_end))
            )
        else:
            # Wraps midnight
            valid_hours = list(range(tw_start, 24)) + list(range(0, tw_end))
            if valid_hours:
                txn_time = txn_time.replace(hour=rng.choice(valid_hours))

        # Amount within ceiling
        if mandate["amount_rule"] == "exact":
            amount = float(mandate["amount_ceiling"])
        else:
            amount = float(rng.integers(
                max(20, int(mandate["amount_ceiling"] * 0.1)),
                mandate["amount_ceiling"] + 1,
            ))

        # Cart and beneficiary
        cart = _generate_cart(rng, mandate, amount, mc)
        beneficiary, is_new_ben = _pick_beneficiary(rng, mandate)

        # Agent profile
        agent_profile = None
        for p in config.agent.profiles:
            if p["type"] == mandate["primary_agent_type"]:
                agent_profile = p
                break
        if agent_profile is None:
            agent_profile = config.agent.profiles[0]

        # Track cumulative spend
        cumulative_spend[mandate_id] = cumulative_spend.get(mandate_id, 0) + amount

        # Session events
        include_retry = rng.random() < agent_profile.get("retry_prob", 0.05)
        session_events = _generate_session_events(
            rng, txn_id, mandate_id, txn_time,
            agent_profile, is_attack=False, attack_class="none",
            include_retry=include_retry,
        )
        all_session_events.extend(session_events)

        transactions.append({
            "transaction_id": txn_id,
            "mandate_id": mandate_id,
            "timestamp": txn_time.isoformat(),
            "timestamp_epoch_ms": int(txn_time.timestamp() * 1000),
            "amount": round(amount, 2),
            "currency": "INR",
            "cart_items": cart["items"],
            "cart_category": cart["category"],
            "cart_mcc": cart["mcc"],
            "cart_matches_purpose": cart["matches_purpose"],
            "beneficiary_id": beneficiary,
            "is_new_beneficiary": is_new_ben,
            "agent_type": agent_profile["type"],
            "session_id": session_events[0]["session_id"] if session_events else None,
            "label": "legitimate",
            "attack_class": "none",
            "hard_negative_type": "none",
            "cumulative_mandate_spend": round(cumulative_spend[mandate_id], 2),
        })

    # ── Generate hard negatives ──────────────────────────────────
    hn_types = tc.hard_negative_types
    for i in range(n_hard_negatives):
        mandate = rng.choice(active_mandates)
        mandate_id = mandate["mandate_id"]
        txn_id = f"TXN_{uuid.uuid4().hex[:12].upper()}"
        hn_type = rng.choice(hn_types)

        granted_at = datetime.fromisoformat(mandate["granted_at"])
        txn_time = _pick_transaction_time(rng, granted_at, now)

        hn_data = _generate_hard_negative(rng, hn_type, mandate, config)
        amount = hn_data["amount"]

        # Force edge-of-window for late_night type
        if hn_data.get("force_edge_hour"):
            tw_end = mandate["time_window_end_hour"]
            edge_hour = max(0, tw_end - 1)
            txn_time = txn_time.replace(hour=edge_hour, minute=int(rng.integers(45, 60)))
        else:
            tw_start = mandate["time_window_start_hour"]
            tw_end = mandate["time_window_end_hour"]
            if tw_end > tw_start:
                txn_time = txn_time.replace(hour=int(rng.integers(tw_start, tw_end)))

        # Hard negatives use legitimate cart/beneficiary
        if hn_type == "first_time_beneficiary":
            beneficiary = f"NEW_{uuid.uuid4().hex[:8].upper()}"
            is_new_ben = True
        else:
            beneficiary, is_new_ben = _pick_beneficiary(rng, mandate)

        cart = _generate_cart(rng, mandate, amount, mc)

        agent_profile = None
        for p in config.agent.profiles:
            if p["type"] == mandate["primary_agent_type"]:
                agent_profile = p
                break
        if agent_profile is None:
            agent_profile = config.agent.profiles[0]

        cumulative_spend[mandate_id] = cumulative_spend.get(mandate_id, 0) + amount

        include_retry = hn_data.get("force_retry", False) or rng.random() < 0.05
        session_events = _generate_session_events(
            rng, txn_id, mandate_id, txn_time,
            agent_profile, is_attack=False, attack_class="none",
            include_retry=include_retry,
        )
        all_session_events.extend(session_events)

        transactions.append({
            "transaction_id": txn_id,
            "mandate_id": mandate_id,
            "timestamp": txn_time.isoformat(),
            "timestamp_epoch_ms": int(txn_time.timestamp() * 1000),
            "amount": round(amount, 2),
            "currency": "INR",
            "cart_items": cart["items"],
            "cart_category": cart["category"],
            "cart_mcc": cart["mcc"],
            "cart_matches_purpose": cart["matches_purpose"],
            "beneficiary_id": beneficiary,
            "is_new_beneficiary": is_new_ben,
            "agent_type": agent_profile["type"],
            "session_id": session_events[0]["session_id"] if session_events else None,
            "label": "legitimate",
            "attack_class": "none",
            "hard_negative_type": hn_type,
            "cumulative_mandate_spend": round(cumulative_spend[mandate_id], 2),
        })

    # ── Generate attack transactions ─────────────────────────────
    attack_classes = list(tc.attack_class_weights.keys())
    attack_weights = list(tc.attack_class_weights.values())

    # Pre-assign attack counts per class
    attack_assignments = rng.choice(
        len(attack_classes), size=n_attacks,
        p=np.array(attack_weights) / sum(attack_weights),
    )

    for i in range(n_attacks):
        attack_class = attack_classes[attack_assignments[i]]

        # A4 attacks specifically target non-active (paused/revoked/expired) mandates
        if attack_class == "A4_off_window_revoked" and rng.random() < 0.5:
            non_active_mandates = mandates_df[
                mandates_df["lifecycle_state"].isin(["paused", "revoked", "expired"])
            ].to_dict("records")
            mandate = rng.choice(non_active_mandates) if non_active_mandates else rng.choice(all_mandates)
        else:
            mandate = rng.choice(active_mandates) if active_mandates else rng.choice(all_mandates)

        mandate_id = mandate["mandate_id"]
        txn_id = f"TXN_{uuid.uuid4().hex[:12].upper()}"

        granted_at = datetime.fromisoformat(mandate["granted_at"])
        txn_time = _pick_transaction_time(rng, granted_at, now)

        # Generate attack-specific data
        attack_generators = {
            "A1_consent_replay": _generate_a1_consent_replay,
            "A2_spoofed_identity": _generate_a2_spoofed_identity,
            "A3_over_ceiling": _generate_a3_over_ceiling,
            "A4_off_window_revoked": _generate_a4_off_window_revoked,
            "A5_slow_drain": _generate_a5_slow_drain,
            "A6_injected_intent": _generate_a6_injected_intent,
        }
        attack_data = attack_generators[attack_class](rng, mandate, txn_time, config)
        amount = attack_data["amount"]

        # Time window handling
        tw_start = mandate["time_window_start_hour"]
        tw_end = mandate["time_window_end_hour"]
        if attack_data.get("force_off_window"):
            if tw_end > tw_start:
                # Pick an hour outside [start, end)
                off_hours = list(range(0, tw_start)) + list(range(tw_end, 24))
                if off_hours:
                    txn_time = txn_time.replace(hour=rng.choice(off_hours))
            else:
                txn_time = txn_time.replace(
                    hour=int(rng.integers(tw_end, tw_start))
                )
        else:
            # Attacks that don't violate time window stay within bounds
            if tw_end > tw_start:
                txn_time = txn_time.replace(
                    hour=int(rng.integers(tw_start, tw_end))
                )
            else:
                valid_hours = list(range(tw_start, 24)) + list(range(0, tw_end))
                if valid_hours:
                    txn_time = txn_time.replace(hour=rng.choice(valid_hours))

        # Cart: A6 gets mismatched cart, others get normal
        is_a6 = attack_class == "A6_injected_intent"
        cart = _generate_cart(rng, mandate, amount, mc, is_a6_attack=is_a6)

        # Beneficiary
        beneficiary, is_new_ben = _pick_beneficiary(rng, mandate, is_a6_attack=is_a6)

        # Agent profile — A2 uses spoofed agent
        if attack_class == "A2_spoofed_identity":
            agent_profile = {
                "type": attack_data.get("spoofed_agent_type", "unknown_bot"),
                "latency_mean_ms": 80,
                "latency_std_ms": 15,
                "step_count_range": (4, 5),
                "retry_prob": 0.01,
            }
        else:
            agent_profile = None
            for p in config.agent.profiles:
                if p["type"] == mandate["primary_agent_type"]:
                    agent_profile = p
                    break
            if agent_profile is None:
                agent_profile = config.agent.profiles[0]

        cumulative_spend[mandate_id] = cumulative_spend.get(mandate_id, 0) + amount

        session_events = _generate_session_events(
            rng, txn_id, mandate_id, txn_time,
            agent_profile, is_attack=True, attack_class=attack_class,
        )
        all_session_events.extend(session_events)

        transactions.append({
            "transaction_id": txn_id,
            "mandate_id": mandate_id,
            "timestamp": txn_time.isoformat(),
            "timestamp_epoch_ms": int(txn_time.timestamp() * 1000),
            "amount": round(amount, 2),
            "currency": "INR",
            "cart_items": cart["items"],
            "cart_category": cart["category"],
            "cart_mcc": cart["mcc"],
            "cart_matches_purpose": cart["matches_purpose"],
            "beneficiary_id": beneficiary,
            "is_new_beneficiary": is_new_ben,
            "agent_type": agent_profile["type"],
            "session_id": session_events[0]["session_id"] if session_events else None,
            "label": "attack",
            "attack_class": attack_class,
            "hard_negative_type": "none",
            "cumulative_mandate_spend": round(cumulative_spend[mandate_id], 2),
        })

    # Build DataFrames
    txn_df = pd.DataFrame(transactions)
    events_df = pd.DataFrame(all_session_events)

    # Sort by timestamp
    txn_df = txn_df.sort_values("timestamp").reset_index(drop=True)
    events_df = events_df.sort_values("timestamp").reset_index(drop=True)

    # Compute true chronological cumulative spend per mandate
    txn_df["cumulative_mandate_spend"] = (
        txn_df.groupby("mandate_id")["amount"].cumsum().round(2)
    )

    # For A5 attacks (slow drain), simulate budget exhaustion
    limit_map = dict(zip(mandates_df["mandate_id"], mandates_df["cumulative_spend_limit"]))
    a5_indices = txn_df[txn_df["attack_class"] == "A5_slow_drain"].index
    for idx in a5_indices:
        m_id = txn_df.at[idx, "mandate_id"]
        lim = limit_map.get(m_id, 100000.0)
        curr = txn_df.at[idx, "cumulative_mandate_spend"]
        if curr <= lim:
            amt = txn_df.at[idx, "amount"]
            txn_df.at[idx, "cumulative_mandate_spend"] = round(lim + amt * float(rng.uniform(1.2, 5.0)), 2)

    # Log stats
    logger.info(
        "transactions_generated",
        total=len(txn_df),
        attacks=int((txn_df["label"] == "attack").sum()),
        legitimate=int((txn_df["label"] == "legitimate").sum()),
        attack_rate=round(
            (txn_df["label"] == "attack").mean() * 100, 2
        ),
        attack_class_dist=txn_df[
            txn_df["label"] == "attack"
        ]["attack_class"].value_counts().to_dict(),
        hard_negative_count=int(
            (txn_df["hard_negative_type"] != "none").sum()
        ),
        session_events_total=len(events_df),
    )

    return txn_df, events_df
