# Agent Transaction Risk Layer — Project Brief

> **Persistent context file.** Every session should read this first.
> Last updated: 2026-08-26

## Competition

Razorpay AI Builder Internship — Track 02: AI Risk Manager

## Problem

AI agents now make payments on users' behalf (UPI agentic payments, ACP, AP2).
Existing fraud detection relies on device fingerprints, clickstream, and
behavioral biometrics — signals that disappear or become misleading when an
agent is the caller. Agentic checkout protocols (ACP) explicitly exclude fraud
modeling from their scope.

**Novel attack class:** Prompt injection that hijacks an agent's payment intent
mid-task. Every existing check (valid credential, valid mandate, in-limit
amount) still passes because only the *intent* was compromised, not the
authority.

## Architecture — Four-Layer Risk System

Includes a resilient Razorpay **test-mode-ready** REST client. The client is
implemented for the following surfaces; live verification remains dependent on
provisioned test credentials, TPAP/device fields, and a test dispute:
- **Mandate APIs** (TPAP Pro): create, fetch, update/revoke, pause/resume, approve, reject
- **Disputes APIs**: fetch all, fetch one, accept, contest-with-evidence

### Layer 1 — Mandate Verifier (Deterministic)

Checks a transaction against the scope of its mandate: amount ceiling, category,
time window, lifecycle state, cumulative spend, cadence.

> **Design note:** Mandates are our stand-in for "an agent's delegated
> authority." There is no native agent object in Razorpay's API, so we model
> authority via the mandate primitive.

### Layer 2 — Behavioral Detector (ML)

Scores session-level behavioral features:
- API call sequence shape
- Timing variance
- Token reuse
- Velocity vs. authorized cadence
- Per-mandate drift from historical baseline

Catches attacks that pass Layer 1 but don't behave like a real agent session.

### Layer 3 — Intent Integrity (Novel Core)

Detects when a valid, in-scope transaction was still the result of a hijacked
goal:
- Semantic divergence between the mandate's stated purpose and the actual cart
- First-time-beneficiary + high-value + off-pattern combinations
- Suspicious content-source provenance
- Explainable embedded-instruction and payment-payload detection

### Layer 4 — Evidence Generator

Assembles a structured evidence packet from all three layers' outputs and
supports Razorpay contest submission through the client. The deterministic
liability routing produces:
- `merchant-defensible`
- `merchant-should-accept`
- `escalate-to-provider`
- `fraud-contest`

## Design Principles

1. **Deterministic first.** Layer 1 runs before any ML — cheap, explainable, fail-closed.
2. **Structured reason trails.** Every layer emits structured JSON, never log strings — Layer 4 depends on this.
3. **Fail closed.** Malformed or missing data → deny. Never silently approve.
4. **Frozen eval split.** The held-out evaluation split is frozen the moment it's created; never touched until final evaluation.
5. **No hardcoded secrets.** Razorpay keys via environment variables only. Committed `.env.example`, gitignored `.env`.
6. **Structured JSON decision logging.** Runtime layer decisions are emitted
   through `structlog`; CLI demos and evaluation scripts may print readable
   output for operators.
7. **Operational safeguards.** State-changing API calls use idempotency,
   retry/backoff and a circuit breaker; webhook signatures and audit records
   are verified/tamper-evident.

## Tech Stack

| Component | Stack |
|-----------|-------|
| Backend / ML | Python 3.11+ — FastAPI, pandas, scikit-learn, LightGBM |
| Console | Plain HTML + JS + CSS (zero-build, upgradeable to React) |
| API Integration | Direct REST client using `requests` (Razorpay test-mode ready) |
| Logging | `structlog` (JSON) |
| Config | `.env` → `python-dotenv` |

## Repo Layout

```
razor/
├── simulator/          # Synthetic data generation & attack scenario simulation
├── layer1_verifier/    # Deterministic mandate-scope checks
├── layer2_detector/    # ML behavioral anomaly scoring
├── layer3_intent/      # Intent integrity / semantic divergence detection
├── layer4_evidence/    # Dispute-evidence assembly & submission
├── console/            # HTML+JS monitoring dashboard
├── observability/      # Structured decision metrics
├── integrations/
│   └── razorpay/       # Razorpay API client wrappers
├── docs/               # Architecture docs, API notes, attack taxonomy
├── scripts/            # Demos, integration probes, regression/drift gates
├── tests/               # Unit, adversarial and chaos test suites
├── .github/            # CI quality gate
├── results/            # Evaluation outputs, metrics, plots
├── pipeline.py         # Versioned real-time four-layer evaluator
├── merchant_policy.py  # Versioned, bounded merchant thresholds
├── platform_security.py # HTTP security/rate-limit controls
├── CLAUDE.md           # ← This file
├── README.md
├── requirements.txt
├── .env.example
└── .gitignore
```

## Implemented Operational Surface

- Authenticated console with live polling, transaction filters, A6 guided
  replay, Layer 3 provenance/timeline drill-down, policy controls, action
  confirmation, and evidence export.
- Versioned `/v1/transactions/evaluate` endpoint which fails closed when a
  required Layer 2 feature vector is absent.
- Frozen Split model regression gate, PSI feature-drift gate, adversarial
  injection corpus, and malformed-input/out-of-order-event chaos tests.
- Structured metrics, local retention cleanup, tamper-evident per-merchant
  audit ledger, request IDs, security headers, and rate limits.

## Boundaries That Must Remain Explicit

- Evaluation metrics use synthetic, frozen simulator data; they are not
  production fraud or false-positive rates.
- Domain reputation, audit/metrics persistence, event streaming, rate limits,
  and tenant controls are local/demo implementations. Production needs managed
  shared infrastructure and database-enforced isolation.
- Formal RBI/PCI compliance and live Razorpay test-mode operations require
  external validation and must never be claimed as completed without it.
