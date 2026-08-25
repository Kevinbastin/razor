# Simulator — Schema, Attack Classes & Regeneration

## Overview

The simulator generates synthetic data for a **quick-commerce merchant**
scenario (grocery/food delivery — mirrors Razorpay's agentic pilot with
Zomato/Swiggy/Zepto). It produces three tables:

## Schema

### `mandates.parquet`

| Column | Type | Description |
|--------|------|-------------|
| `mandate_id` | string | UPI Mandate Notification (UMN) identifier |
| `payer_vpa` | string | Payer's Virtual Payment Address |
| `payee_vpa` | string | Merchant's VPA |
| `amount_ceiling` | int | Maximum permitted amount per transaction (INR) |
| `amount_rule` | string | `"max"` (up to ceiling) or `"exact"` (fixed amount) |
| `permitted_mccs` | json-list | Allowed Merchant Category Codes |
| `permitted_categories` | json-list | Human-readable category names |
| `time_window_start_hour` | int | Earliest permitted hour (0-23) |
| `time_window_end_hour` | int | Latest permitted hour (0-24) |
| `cadence` | string | `daily` / `weekly` / `biweekly` / `monthly` / `on_demand` |
| `purpose` | string | Stated purpose (e.g. "weekly grocery top-up, ~₹2000") |
| `purpose_template_idx` | int | Index into purpose template pool |
| `lifecycle_state` | string | `active` / `paused` / `revoked` / `expired` |
| `granted_at` | string (ISO) | When the mandate was created |
| `expires_at` | string (ISO) | Mandate expiry timestamp |
| `duration_days` | int | Mandate validity period |
| `beneficiaries` | json-list | Known legitimate beneficiary IDs |
| `primary_agent_type` | string | Agent type primarily using this mandate |
| `cumulative_spend_limit` | int | Estimated total spend limit over mandate lifetime |

### `transactions.parquet`

| Column | Type | Description |
|--------|------|-------------|
| `transaction_id` | string | Unique transaction identifier |
| `mandate_id` | string | FK → mandates |
| `timestamp` | string (ISO) | Transaction time |
| `timestamp_epoch_ms` | int | Unix epoch milliseconds |
| `amount` | float | Transaction amount (INR) |
| `currency` | string | Always `"INR"` |
| `cart_items` | json-list | Items in the cart |
| `cart_category` | string | Cart MCC category name |
| `cart_mcc` | string | Merchant Category Code |
| `cart_matches_purpose` | bool | Whether cart matches mandate purpose |
| `beneficiary_id` | string | Beneficiary identifier |
| `is_new_beneficiary` | bool | First-time beneficiary flag |
| `agent_type` | string | Agent type that executed the transaction |
| `session_id` | string | FK → session_events |
| `label` | string | `"legitimate"` or `"attack"` |
| `attack_class` | string | Attack class or `"none"` |
| `hard_negative_type` | string | Hard negative type or `"none"` |
| `cumulative_mandate_spend` | float | Running cumulative spend on this mandate |

### `session_events.parquet`

| Column | Type | Description |
|--------|------|-------------|
| `session_id` | string | Session identifier |
| `transaction_id` | string | FK → transactions |
| `mandate_id` | string | FK → mandates |
| `step_index` | int | Step sequence number within session |
| `step_name` | string | API call step name |
| `timestamp` | string (ISO) | Step timestamp |
| `timestamp_epoch_ms` | int | Unix epoch milliseconds |
| `latency_ms` | float | Inter-step latency in milliseconds |
| `status` | string | `"success"` or `"failed_network"` |
| `consent_token` | string/null | Consent token (only on `check_consent` step) |
| `agent_type` | string | Agent type |
| `is_retry` | bool | Whether this step is a retry |

## Attack Classes

| Class | Name | Description | Layer 1 Catchable? |
|-------|------|-------------|-------------------|
| **A1** | Consent-token replay | Reuses a consent token from a previous session | Partial (token tracking) |
| **A2** | Spoofed agent identity | Mismatched signing key / agent ID, abnormal timing | No (behavioral) |
| **A3** | Over-ceiling burst | Amount exceeds mandate ceiling (10-300% over) | **Yes** |
| **A4** | Off-window / revoked | Transaction outside time window or on revoked mandate | **Yes** |
| **A5** | Slow drain | Many small in-limit charges accumulating past cumulative limit | **Yes** (cumulative) |
| **A6** | Injected intent | Valid credentials, active mandate, in-limit amount — but cart/beneficiary mismatch. Simulates prompt injection hijacking agent goal | **No** (Layer 3 only) |

### Hard Negatives

Legitimate transactions that superficially resemble attacks:

| Type | Mimics | Why it's legitimate |
|------|--------|-------------------|
| `bulk_festival_order` | A3/A5 | High amount near ceiling — Diwali/holiday stocking |
| `first_time_beneficiary` | A6 | New but genuine beneficiary |
| `late_night_order` | A4 | Edge of time window but still within bounds |
| `rapid_retry_after_fail` | A1 | Quick retry after genuine network failure |
| `high_value_single` | A3 | Near-ceiling single purchase, still under limit |

## Regeneration

To regenerate the synthetic dataset deterministically:

```bash
# From project root, with venv activated
python -m simulator.run --seed 42 --output-dir simulator/data
```

The seed `42` is the default. Changing the seed produces a different but
structurally identical dataset. The `generation_metadata.json` file in the
output directory records the exact config used.

> **⚠️ WARNING:** After generating data for the first time, `results/holdout_split.json`
> is created with the held-out test mandate IDs. **DO NOT regenerate or modify this
> file.** The evaluation split is frozen per Design Principle #4.

## Agent Behavioral Profiles

| Agent Type | Latency (mean ms) | Latency (std ms) | Steps | Retry Prob |
|------------|-------------------|-------------------|-------|------------|
| `fast_bot` | 120 | 30 | 4-5 | 2% |
| `standard_agent` | 800 | 250 | 4-7 | 5% |
| `cautious_agent` | 2000 | 600 | 5-8 | 8% |
| `legacy_integration` | 3500 | 1200 | 4-6 | 12% |

## Merchant Category Codes (MCCs)

### Permitted (legitimate)
- `5411` — Grocery stores (40%)
- `5812` — Restaurants / food delivery (25%)
- `5499` — Convenience stores (15%)
- `5912` — Pharmacy (10%)
- `5462` — Bakeries (5%)
- `5921` — Liquor stores (5%)

### Off-category (attack A6)
- `5944` — Jewelry, `5732` — Electronics, `7995` — Gambling,
  `5311` — Department stores, `4722` — Travel agencies
