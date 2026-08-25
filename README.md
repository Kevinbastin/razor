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

- **Backend:** Python 3.11, FastAPI, pandas, scikit-learn, xgboost
- **Console:** Vanilla HTML + JS + CSS
- **APIs:** Razorpay Mandate & Disputes (test mode)

## License

Private — Hackathon submission.
