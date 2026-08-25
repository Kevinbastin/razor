"""
Simulator configuration — every tunable parameter lives here.

Design: config-driven + versioned so results are fully reproducible.
Change CONFIG_VERSION when schema changes.
"""

from dataclasses import dataclass, field
from typing import Dict, List


CONFIG_VERSION = "1.0.0"


@dataclass(frozen=True)
class MandateConfig:
    """Parameters for mandate generation."""

    count: int = 500
    # Amount ceilings (INR) — distribution buckets
    amount_ceiling_buckets: List[dict] = field(default_factory=lambda: [
        {"min": 200, "max": 500, "weight": 0.15},     # small top-ups
        {"min": 500, "max": 2000, "weight": 0.35},    # regular grocery
        {"min": 2000, "max": 5000, "weight": 0.30},   # weekly stock-up
        {"min": 5000, "max": 15000, "weight": 0.15},  # bulk/family
        {"min": 15000, "max": 50000, "weight": 0.05}, # premium/business
    ])
    # Amount rules
    amount_rule_weights: Dict[str, float] = field(default_factory=lambda: {
        "max": 0.80,   # up to ceiling
        "exact": 0.20, # exactly this amount each time
    })
    # Lifecycle states at generation time
    lifecycle_weights: Dict[str, float] = field(default_factory=lambda: {
        "active": 0.82,
        "paused": 0.08,
        "revoked": 0.06,
        "expired": 0.04,
    })
    # Cadence rules
    cadence_weights: Dict[str, float] = field(default_factory=lambda: {
        "daily": 0.10,
        "weekly": 0.35,
        "biweekly": 0.20,
        "monthly": 0.25,
        "on_demand": 0.10,
    })
    # Time window spans (hours from midnight)
    time_window_profiles: List[dict] = field(default_factory=lambda: [
        {"start_hour": 6, "end_hour": 23, "weight": 0.50},   # daytime
        {"start_hour": 0, "end_hour": 24, "weight": 0.25},   # any time
        {"start_hour": 8, "end_hour": 14, "weight": 0.15},   # morning
        {"start_hour": 17, "end_hour": 22, "weight": 0.10},  # evening
    ])
    # Mandate duration (days from granted_at to expiry)
    duration_days_range: tuple = (30, 365)
    # History window for generating granted_at timestamps
    history_days: int = 180


@dataclass(frozen=True)
class TransactionConfig:
    """Parameters for transaction generation."""

    target_count: int = 50000
    # Attack rate (fraction of total)
    attack_rate: float = 0.03  # ~3%, gives 2-4% range with variance
    # Attack class distribution (must sum to 1.0)
    attack_class_weights: Dict[str, float] = field(default_factory=lambda: {
        "A1_consent_replay": 0.15,
        "A2_spoofed_identity": 0.12,
        "A3_over_ceiling": 0.18,
        "A4_off_window_revoked": 0.18,
        "A5_slow_drain": 0.15,
        "A6_injected_intent": 0.22,  # Heaviest — our novel case
    })
    # Hard negative rate (fraction of legitimate transactions)
    hard_negative_rate: float = 0.04
    # Hard negative types
    hard_negative_types: List[str] = field(default_factory=lambda: [
        "bulk_festival_order",     # Looks like A3/A5 but legitimate
        "first_time_beneficiary",  # Looks like A6 but legitimate
        "late_night_order",        # Looks like A4 but in-window
        "rapid_retry_after_fail",  # Looks like A1 but genuine retry
        "high_value_single",       # Looks like A3 but under ceiling
    ])


@dataclass(frozen=True)
class AgentConfig:
    """Agent type profiles with behavioral signatures."""

    profiles: List[dict] = field(default_factory=lambda: [
        {
            "type": "fast_bot",
            "weight": 0.30,
            "latency_mean_ms": 120,
            "latency_std_ms": 30,
            "step_count_range": (4, 5),
            "retry_prob": 0.02,
        },
        {
            "type": "standard_agent",
            "weight": 0.40,
            "latency_mean_ms": 800,
            "latency_std_ms": 250,
            "step_count_range": (4, 7),
            "retry_prob": 0.05,
        },
        {
            "type": "cautious_agent",
            "weight": 0.20,
            "latency_mean_ms": 2000,
            "latency_std_ms": 600,
            "step_count_range": (5, 8),
            "retry_prob": 0.08,
        },
        {
            "type": "legacy_integration",
            "weight": 0.10,
            "latency_mean_ms": 3500,
            "latency_std_ms": 1200,
            "step_count_range": (4, 6),
            "retry_prob": 0.12,
        },
    ])


@dataclass(frozen=True)
class MerchantConfig:
    """Quick-commerce merchant categories and products."""

    # Permitted MCC codes for this merchant scenario
    permitted_categories: List[dict] = field(default_factory=lambda: [
        {"mcc": "5411", "name": "grocery_stores", "weight": 0.40},
        {"mcc": "5812", "name": "restaurants_food_delivery", "weight": 0.25},
        {"mcc": "5499", "name": "convenience_stores", "weight": 0.15},
        {"mcc": "5462", "name": "bakeries", "weight": 0.05},
        {"mcc": "5921", "name": "liquor_stores", "weight": 0.05},
        {"mcc": "5912", "name": "pharmacy", "weight": 0.10},
    ])
    # Off-category MCCs for attack scenarios
    off_categories: List[dict] = field(default_factory=lambda: [
        {"mcc": "5944", "name": "jewelry_stores"},
        {"mcc": "5732", "name": "electronics"},
        {"mcc": "7995", "name": "gambling"},
        {"mcc": "5311", "name": "department_stores"},
        {"mcc": "4722", "name": "travel_agencies"},
    ])
    # Product baskets for purpose matching
    purpose_templates: List[dict] = field(default_factory=lambda: [
        {
            "purpose": "weekly grocery top-up, ~₹{amount}",
            "categories": ["grocery_stores", "convenience_stores"],
            "typical_items": [
                "rice", "dal", "oil", "vegetables", "fruits",
                "milk", "bread", "eggs", "spices", "snacks",
            ],
        },
        {
            "purpose": "daily food delivery, up to ₹{amount}",
            "categories": ["restaurants_food_delivery"],
            "typical_items": [
                "biryani", "pizza", "burger", "thali", "dosa",
                "noodles", "salad", "dessert", "juice", "coffee",
            ],
        },
        {
            "purpose": "household essentials, max ₹{amount}/month",
            "categories": ["grocery_stores", "convenience_stores", "pharmacy"],
            "typical_items": [
                "detergent", "soap", "shampoo", "toothpaste",
                "tissue", "batteries", "bulbs", "cleaning supplies",
            ],
        },
        {
            "purpose": "pharmacy and health supplies, up to ₹{amount}",
            "categories": ["pharmacy"],
            "typical_items": [
                "vitamins", "bandages", "sanitizer", "masks",
                "pain relief", "cough syrup", "thermometer",
            ],
        },
        {
            "purpose": "bakery and snacks subscription, ~₹{amount}/week",
            "categories": ["bakeries", "convenience_stores"],
            "typical_items": [
                "bread", "cake", "cookies", "pastry", "muffins",
                "biscuits", "chips", "namkeen",
            ],
        },
    ])
    # Off-purpose items for A6 attacks
    off_purpose_items: List[str] = field(default_factory=lambda: [
        "gold chain", "diamond ring", "laptop", "smartphone",
        "gaming console", "luxury watch", "designer handbag",
        "cryptocurrency voucher", "gift card bulk", "airline ticket",
        "hotel booking", "casino chips",
    ])
    # Beneficiary pool
    legitimate_beneficiary_count: int = 50
    suspicious_beneficiary_count: int = 20


@dataclass(frozen=True)
class SessionConfig:
    """API call sequence configuration."""

    # Standard flow steps
    standard_flow: List[str] = field(default_factory=lambda: [
        "discover_mandate",
        "validate_mandate",
        "build_cart",
        "compute_amount",
        "check_consent",
        "initiate_payment",
        "confirm_payment",
    ])
    # Minimum required steps (subset of standard_flow)
    required_steps: List[str] = field(default_factory=lambda: [
        "discover_mandate",
        "validate_mandate",
        "initiate_payment",
        "confirm_payment",
    ])


@dataclass(frozen=True)
class SimulatorConfig:
    """Top-level simulator configuration."""

    seed: int = 42
    version: str = CONFIG_VERSION
    mandate: MandateConfig = field(default_factory=MandateConfig)
    transaction: TransactionConfig = field(default_factory=TransactionConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    merchant: MerchantConfig = field(default_factory=MerchantConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    holdout_fraction: float = 0.20  # 20% of mandates held out
    output_format: str = "parquet"  # "parquet" or "sqlite"
