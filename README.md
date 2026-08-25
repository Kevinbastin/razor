# Agent Transaction Risk Layer

> Razorpay AI Builder Internship — Track 02: AI Risk Manager

A four-layer risk system for AI-agent-initiated payments, detecting fraud that
bypasses traditional signals by exploiting delegated authority and hijacked intent.

## Layers

| # | Layer | Type | Purpose |
|---|-------|------|---------|
| 1 | Mandate Verifier | Deterministic | Scope-check against mandate constraints |
| 2 | Behavioral Detector | ML | Session-level anomaly scoring |
| 3 | Intent Integrity | ML + Heuristic | Detect hijacked payment goals |
| 4 | Evidence Generator | Assembly | Dispute packet creation & submission |

## Quick Start

```bash
# Clone & setup
git clone <repo-url> && cd razor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Configure Razorpay test keys
cp .env.example .env
# Edit .env with your test-mode keys

# Run API server
uvicorn main:app --reload

# Open console
# Open console/index.html in your browser
```

## Tech Stack

- **Backend:** Python 3.12, FastAPI, pandas, scikit-learn, xgboost
- **Console:** Vanilla HTML + JS + CSS
- **APIs:** Razorpay Mandate & Disputes (test mode)

## Synthetic Data

Generate the training dataset (500 mandates, ~50K transactions, ~263K session events):

```bash
python -m simulator.run --seed 42 --output-dir simulator/data
```

See [docs/simulator.md](docs/simulator.md) for full schema, attack classes, and regeneration guide.

> **⚠️ WARNING: DO NOT regenerate or modify `results/holdout_split.json` after
> initial creation.** The held-out evaluation split (20% of mandates) is frozen
> per Design Principle #4. Touching it invalidates all evaluation results.

## License

Private — Hackathon submission.
