"""
Mandate generator — creates ~500 synthetic mandates modelling
an agent's delegated authority over a quick-commerce merchant.

Each mandate has: UMN, payer/payee VPA, amount ceiling, amount rule,
permitted MCCs, time window, cadence, stated purpose, lifecycle state,
granted_at timestamp.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

import numpy as np
import pandas as pd
import structlog

from simulator.config import MandateConfig, MerchantConfig, SimulatorConfig

logger = structlog.get_logger(__name__)

# Indian timezone offset
IST = timezone(timedelta(hours=5, minutes=30))


def _weighted_choice(rng: np.random.Generator, items: list, weights: list, size: int = 1):
    """Weighted random selection using numpy Generator."""
    probs = np.array(weights, dtype=float)
    probs /= probs.sum()
    indices = rng.choice(len(items), size=size, p=probs)
    if size == 1:
        return items[indices[0]]
    return [items[i] for i in indices]


def _generate_umn() -> str:
    """Generate a realistic UPI Mandate Notification (UMN) identifier."""
    return f"UMN{uuid.uuid4().hex[:16].upper()}"


def _generate_vpa(rng: np.random.Generator, prefix: str, idx: int) -> str:
    """Generate a realistic-looking VPA."""
    domains = ["okaxis", "okicici", "okhdfcbank", "ybl", "paytm", "apl"]
    names = [
        "rahul", "priya", "amit", "sneha", "vikram", "meera",
        "arjun", "kavya", "ravi", "anita", "deepak", "pooja",
        "suresh", "neha", "karan", "divya", "manish", "swati",
        "rajesh", "nisha", "arun", "shruti", "mohit", "geeta",
    ]
    name = rng.choice(names)
    suffix = rng.integers(1, 9999)
    domain = rng.choice(domains)
    return f"{name}{suffix}@{domain}"


def _generate_payee_vpa(rng: np.random.Generator) -> str:
    """Generate a merchant payee VPA."""
    merchants = [
        "zepto.merchant", "swiggy.food", "blinkit.grocery",
        "bigbasket.order", "dunzo.delivery", "jiomart.shop",
        "instamart.swiggy", "freshexpress.qcom", "milkbasket.daily",
    ]
    merchant = rng.choice(merchants)
    return f"{merchant}@razorpay"


def generate_mandates(config: SimulatorConfig) -> pd.DataFrame:
    """
    Generate synthetic mandates.

    Returns:
        DataFrame with columns matching the mandate schema.
    """
    rng = np.random.default_rng(config.seed)
    mc = config.mandate
    merc = config.merchant

    records = []
    # Base time: ~6 months ago from "now"
    now = datetime(2025, 8, 25, 12, 0, 0, tzinfo=IST)
    history_start = now - timedelta(days=mc.history_days)

    # Pre-generate beneficiary pools
    legit_beneficiaries = [
        f"BEN_{hashlib.md5(f'legit_{i}'.encode()).hexdigest()[:8].upper()}"
        for i in range(merc.legitimate_beneficiary_count)
    ]

    for i in range(mc.count):
        umn = _generate_umn()
        payer_vpa = _generate_vpa(rng, "payer", i)
        payee_vpa = _generate_payee_vpa(rng)

        # Amount ceiling — weighted bucket selection
        bucket_weights = [b["weight"] for b in mc.amount_ceiling_buckets]
        bucket = _weighted_choice(
            rng, mc.amount_ceiling_buckets, bucket_weights
        )
        amount_ceiling = int(rng.integers(bucket["min"], bucket["max"] + 1))

        # Amount rule
        rule_keys = list(mc.amount_rule_weights.keys())
        rule_weights = list(mc.amount_rule_weights.values())
        amount_rule = _weighted_choice(rng, rule_keys, rule_weights)

        # Permitted categories (1-3 MCCs per mandate)
        n_cats = rng.integers(1, 4)
        cat_weights = [c["weight"] for c in merc.permitted_categories]
        cat_indices = rng.choice(
            len(merc.permitted_categories),
            size=min(n_cats, len(merc.permitted_categories)),
            replace=False,
            p=np.array(cat_weights) / sum(cat_weights),
        )
        permitted_mccs = [merc.permitted_categories[j]["mcc"] for j in cat_indices]
        permitted_cat_names = [merc.permitted_categories[j]["name"] for j in cat_indices]

        # Time window
        tw_weights = [tw["weight"] for tw in mc.time_window_profiles]
        time_window = _weighted_choice(rng, mc.time_window_profiles, tw_weights)

        # Cadence
        cad_keys = list(mc.cadence_weights.keys())
        cad_weights = list(mc.cadence_weights.values())
        cadence = _weighted_choice(rng, cad_keys, cad_weights)

        # Purpose string
        purpose_template = rng.choice(merc.purpose_templates)
        purpose = purpose_template["purpose"].format(amount=amount_ceiling)

        # Lifecycle state
        state_keys = list(mc.lifecycle_weights.keys())
        state_weights = list(mc.lifecycle_weights.values())
        lifecycle_state = _weighted_choice(rng, state_keys, state_weights)

        # Granted-at timestamp (random within history window)
        days_ago = rng.integers(0, mc.history_days)
        granted_at = history_start + timedelta(
            days=int(days_ago),
            hours=int(rng.integers(8, 20)),
            minutes=int(rng.integers(0, 60)),
        )

        # Mandate duration
        duration_days = int(rng.integers(
            mc.duration_days_range[0], mc.duration_days_range[1] + 1
        ))
        expires_at = granted_at + timedelta(days=duration_days)

        # Assign beneficiaries (1-5 legitimate beneficiaries per mandate)
        n_bens = int(rng.integers(1, 6))
        mandate_beneficiaries = list(
            rng.choice(legit_beneficiaries, size=n_bens, replace=False)
        )

        # Agent type assignment (each mandate is primarily used by one agent type)
        agent_profiles = config.agent.profiles
        agent_weights = [p["weight"] for p in agent_profiles]
        primary_agent = _weighted_choice(rng, agent_profiles, agent_weights)

        records.append({
            "mandate_id": umn,
            "payer_vpa": payer_vpa,
            "payee_vpa": payee_vpa,
            "amount_ceiling": amount_ceiling,
            "amount_rule": amount_rule,
            "permitted_mccs": permitted_mccs,
            "permitted_categories": permitted_cat_names,
            "time_window_start_hour": time_window["start_hour"],
            "time_window_end_hour": time_window["end_hour"],
            "cadence": cadence,
            "purpose": purpose,
            "purpose_template_idx": int(
                merc.purpose_templates.index(purpose_template)
            ),
            "lifecycle_state": lifecycle_state,
            "granted_at": granted_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "duration_days": duration_days,
            "beneficiaries": mandate_beneficiaries,
            "primary_agent_type": primary_agent["type"],
            "cumulative_spend_limit": (
                amount_ceiling * _cadence_multiplier(cadence, duration_days)
            ),
        })

    df = pd.DataFrame(records)
    logger.info(
        "mandates_generated",
        count=len(df),
        lifecycle_dist=df["lifecycle_state"].value_counts().to_dict(),
        cadence_dist=df["cadence"].value_counts().to_dict(),
    )
    return df


def _cadence_multiplier(cadence: str, duration_days: int) -> int:
    """Estimate max number of transactions for cumulative limit."""
    cycles = {
        "daily": duration_days,
        "weekly": duration_days // 7,
        "biweekly": duration_days // 14,
        "monthly": duration_days // 30,
        "on_demand": duration_days // 3,  # conservative estimate
    }
    return max(cycles.get(cadence, duration_days // 7), 1)
