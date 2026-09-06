"""
Agent Transaction Risk Layer — FastAPI Entry Point

Usage:
    uvicorn main:app --reload
"""

import os
import json
import secrets
import base64
import uuid
import math
from copy import deepcopy
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import structlog

from logging_config import setup_logging
from observability.metrics import METRICS
from platform_security import SECURITY_HEADERS, SlidingWindowRateLimiter
from merchant_policy import MerchantPolicyStore

# Load environment variables from .env (if present)
load_dotenv()

# Initialize structured logging
setup_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))

logger = structlog.get_logger(__name__)
security = HTTPBasic()
ROOT = Path(__file__).parent
AUDIT_DB = ROOT / "results/evidence_audit.sqlite"
CONSOLE_STATE_PATH = ROOT / "results/console_demo_state.json"
READ_LIMITER = SlidingWindowRateLimiter(int(os.getenv("API_RATE_LIMIT_PER_MINUTE", "120")), 60)
ACTION_LIMITER = SlidingWindowRateLimiter(int(os.getenv("ACTION_RATE_LIMIT_PER_MINUTE", "20")), 60)

def console_user(credentials: HTTPBasicCredentials = Depends(security)):
    expected_user = os.getenv("CONSOLE_USERNAME", "demo")
    expected_password = os.getenv("CONSOLE_PASSWORD", "demo")
    if not (secrets.compare_digest(credentials.username, expected_user) and secrets.compare_digest(credentials.password, expected_password)):
        raise HTTPException(status_code=401, detail="Console login required", headers={"WWW-Authenticate": "Basic"})
    return credentials.username

def demo_packet() -> dict:
    path = ROOT / "results/a6_evidence_packet.json"
    if path.exists(): return json.loads(path.read_text())
    return {"transaction": {"transaction_id": "DEMO_A6", "amount": 4900, "cart_items": ["gift card bulk"]}, "layer1": {"verdict":"pass"}, "layer2":{"verdict":"pass", "risk_score":.12, "top_risk_factors":[]}, "layer3":{"verdict":"flagged", "signals_triggered":["I1"]}, "liability_determination":"escalate-to-provider", "transaction_disposition":"blocked", "mandate_snapshot":{"mandate_id":"DEMO_MANDATE", "lifecycle_state":"active"}, "narrative":{"status":"not_generated"}}

def _load_console_state() -> dict:
    if not CONSOLE_STATE_PATH.exists():
        return {"transactions": {}, "mandates": {}, "actions": []}
    try:
        loaded = json.loads(CONSOLE_STATE_PATH.read_text())
        return {"transactions": loaded.get("transactions", {}), "mandates": loaded.get("mandates", {}), "actions": loaded.get("actions", [])}
    except (OSError, json.JSONDecodeError):
        logger.warning("console_demo_state_invalid")
        return {"transactions": {}, "mandates": {}, "actions": []}

def _save_console_state(state: dict) -> None:
    CONSOLE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = CONSOLE_STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    temporary.replace(CONSOLE_STATE_PATH)

def _json_safe(value):
    """Convert NaN/Infinity from data-science artifacts into valid JSON nulls."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value

def console_rows() -> list[dict]:
    packet = demo_packet(); txn = packet["transaction"]
    a6 = {"id": txn.get("transaction_id", "DEMO_A6"), "amount": txn.get("amount", 4900), "cart": txn.get("cart_items", []), "timestamp": txn.get("timestamp", "Demo session"), "l1": packet["layer1"]["verdict"], "l2": packet["layer2"]["verdict"], "l3": packet["layer3"]["verdict"], "status": packet.get("transaction_disposition", "blocked"), "packet": packet}
    clear = {"id":"TXN_CLEAR_GROCERY", "amount": 1280, "cart":["rice", "milk", "vegetables"], "timestamp":"Recent", "l1":"pass", "l2":"pass", "l3":"clear", "status":"approved", "packet": {**packet, "transaction":{"transaction_id":"TXN_CLEAR_GROCERY","amount":1280,"cart_items":["rice","milk","vegetables"]}, "layer1":{"verdict":"pass","failed_checks":[],"evidence":{}}, "layer2":{"verdict":"pass","risk_score":.08,"top_risk_factors":[]}, "layer3":{"verdict":"clear","signals_triggered":[],"evidence":{}}, "liability_determination":"merchant-defensible", "transaction_disposition":"approved"}}
    stepup = {"id":"TXN_STEP_UP", "amount": 3200, "cart":["festival grocery order"], "timestamp":"Recent", "l1":"pass", "l2":"suspicious", "l3":"clear", "status":"pending re-authorization", "packet": {**clear["packet"], "transaction":{"transaction_id":"TXN_STEP_UP","amount":3200,"cart_items":["festival grocery order"]}, "layer2":{"verdict":"suspicious","risk_score":.31,"top_risk_factors":[{"feature":"vel_amount_z_score","value":2.1,"importance_rank":1}]}, "transaction_disposition":"pending re-authorization"}}
    rows = [a6, stepup, clear]
    state = _load_console_state()
    for row in rows:
        update = state["transactions"].get(row["id"], {})
        if update:
            row.update({key: value for key, value in update.items() if key in {"status", "timestamp"}})
            row["packet"] = deepcopy(row["packet"])
            row["packet"]["transaction_disposition"] = row["status"]
        mandate_id = row["packet"].get("mandate_snapshot", {}).get("mandate_id")
        mandate_update = state["mandates"].get(mandate_id, {})
        if mandate_update:
            row["packet"] = deepcopy(row["packet"])
            row["packet"]["mandate_snapshot"]["lifecycle_state"] = mandate_update["state"]
    return rows


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"} and (
        os.getenv("CONSOLE_USERNAME", "demo") == "demo" or os.getenv("CONSOLE_PASSWORD", "demo") == "demo"
    ):
        raise RuntimeError("Refusing production startup with default console credentials")
    if os.getenv("APP_ENV", "development").lower() in {"production", "prod"} and not os.getenv("AUDIT_HMAC_SECRET"):
        raise RuntimeError("Refusing production startup without AUDIT_HMAC_SECRET")
    from layer4_evidence import EvidenceStore
    retention_days = int(os.getenv("AUDIT_RETENTION_DAYS", "90"))
    purged = EvidenceStore(AUDIT_DB).purge_older_than(retention_days)
    logger.info(
        "server_startup",
        service="agent-transaction-risk-layer",
        env=os.getenv("APP_ENV", "development"),
        audit_retention_days=retention_days,
        audit_records_purged=purged,
    )
    yield
    logger.info("server_shutdown")


app = FastAPI(
    title="Agent Transaction Risk Layer",
    description=(
        "Four-layer risk system for AI-agent-initiated payments. "
        "Built for the Razorpay AI Builder Internship — Track 02: AI Risk Manager."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# The console is same-origin. Explicit origins avoid credentialed wildcard CORS.
allowed_origins = [origin.strip() for origin in os.getenv(
    "ALLOWED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000"
).split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def console_guard(request: Request, call_next):
    """Authenticate console traffic, limit API calls, and attach safe headers."""
    if request.url.path == "/health":
        return await call_next(request)
    webhook_request = request.url.path == "/v1/webhooks/razorpay"
    expected = f"{os.getenv('CONSOLE_USERNAME', 'demo')}:{os.getenv('CONSOLE_PASSWORD', 'demo')}"
    header = request.headers.get("authorization", "")
    try:
        supplied = base64.b64decode(header.removeprefix("Basic ")).decode() if header.startswith("Basic ") else ""
    except Exception:
        supplied = ""
    if not webhook_request and not secrets.compare_digest(supplied, expected):
        return Response(status_code=401, headers={"WWW-Authenticate": "Basic realm=ATRL Console"})
    if request.url.path.startswith("/api/") or request.url.path.startswith("/v1/"):
        client = request.client.host if request.client else "unknown"
        limiter = ACTION_LIMITER if request.method in {"POST", "PUT", "PATCH", "DELETE"} else READ_LIMITER
        allowed, retry_after = limiter.allow(f"{client}:{request.method}")
        if not allowed:
            logger.warning("api_rate_limited", path=request.url.path, method=request.method, client=client)
            return Response(status_code=429, headers={"Retry-After": str(retry_after)}, content="Rate limit exceeded")
    response = await call_next(request)
    request_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    response.headers["X-Request-Id"] = request_id
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {
        "status": "healthy",
        "service": "agent-transaction-risk-layer",
        "version": "0.1.0",
    }

@app.get("/v1/health")
async def v1_health_check():
    return await health_check()

@app.get("/v1/metrics", dependencies=[Depends(console_user)])
async def metrics_snapshot():
    """Authenticated process-local metrics snapshot for the demo console/ops."""
    return METRICS.snapshot()

@app.get("/v1/policies/current", dependencies=[Depends(console_user)])
async def get_current_policy():
    return MerchantPolicyStore().get(os.getenv("MERCHANT_ID", "demo-merchant"))

@app.put("/v1/policies/current", dependencies=[Depends(console_user)])
async def update_current_policy(body: dict):
    try:
        return MerchantPolicyStore().update(os.getenv("MERCHANT_ID", "demo-merchant"), body)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post("/v1/transactions/evaluate", dependencies=[Depends(console_user)])
async def evaluate_transaction_api(body: dict):
    """Evaluate an ingested transaction through all four reason-trail layers."""
    from pipeline import evaluate_transaction
    try:
        return evaluate_transaction(body, merchant_id=os.getenv("MERCHANT_ID", "demo-merchant"))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc

@app.post("/v1/webhooks/razorpay")
async def razorpay_webhook(request: Request):
    """Authenticate and audit incoming Razorpay events before any processing."""
    from integrations.razorpay import WebhookVerificationError, verify_webhook_signature
    raw_body = await request.body()
    try:
        verify_webhook_signature(raw_body, request.headers.get("X-Razorpay-Signature"))
        event = json.loads(raw_body)
    except (WebhookVerificationError, json.JSONDecodeError) as exc:
        logger.warning("razorpay_webhook_rejected", reason=type(exc).__name__)
        raise HTTPException(status_code=400, detail="Invalid webhook") from exc
    event_name = str(event.get("event", "unknown"))
    entity = event.get("payload", {})
    logger.info("razorpay_webhook_authenticated", event=event_name, entity_keys=sorted(entity)[:10])
    return {"status": "accepted", "event": event_name}

@app.get("/api/console", dependencies=[Depends(console_user)])
async def console_data():
    rows = console_rows()
    metadata_path = ROOT / "results/layer2_model.joblib.metadata.json"
    model_metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    return _json_safe({
        "transactions": rows,
        "disputes": [r for r in rows if r["status"] in {"blocked", "pending review"}],
        "mandates": [{"mandate_id": r["packet"].get("mandate_snapshot", {}).get("mandate_id", "DEMO_MANDATE"), "purpose": r["packet"].get("mandate_snapshot", {}).get("purpose", "weekly grocery top-up"), "state": r["packet"].get("mandate_snapshot", {}).get("lifecycle_state", "active")} for r in rows[:1]],
        "value": {"auto_resolved": 1, "false_positives_avoided": 1, "rupees_defended": sum(r["amount"] for r in rows if r["status"] == "blocked")},
        "policy": MerchantPolicyStore().get(os.getenv("MERCHANT_ID", "demo-merchant")),
        "system": {"model_version": model_metadata.get("model_version", "unavailable"), "data_snapshot_version": model_metadata.get("data_snapshot_version", "unavailable"), "webhook_configured": bool(os.getenv("RAZORPAY_WEBHOOK_SECRET")), "razorpay_circuit": "not-instantiated", "audit_ledger": "keyed" if os.getenv("AUDIT_HMAC_SECRET") else "development-unkeyed", "console_mode": "interactive demo"},
    })

@app.post("/api/demo/actions", dependencies=[Depends(console_user)])
async def demo_console_action(body: dict, reviewer: str = Depends(console_user)):
    """Persist a safe local demo action; it never calls Razorpay or moves money."""
    target_type, target_id, action = body.get("target_type"), body.get("target_id"), body.get("action")
    permitted = {
        "transaction": {"contest", "accept", "reauthorize"},
        "mandate": {"pause", "resume", "revoke"},
    }
    if target_type not in permitted or action not in permitted[target_type] or not isinstance(target_id, str):
        raise HTTPException(400, "Unsupported demo action")
    rows = console_rows()
    state = _load_console_state()
    if target_type == "transaction":
        if not any(row["id"] == target_id for row in rows):
            raise HTTPException(404, "Unknown demo transaction")
        status = {"contest": "pending review", "accept": "accepted", "reauthorize": "approved"}[action]
        state["transactions"][target_id] = {"status": status, "timestamp": "Updated just now"}
    else:
        mandate_ids = {row["packet"].get("mandate_snapshot", {}).get("mandate_id") for row in rows}
        if target_id not in mandate_ids:
            raise HTTPException(404, "Unknown demo mandate")
        state["mandates"][target_id] = {"state": "paused" if action == "pause" else "active" if action == "resume" else "revoked"}
    entry = {"target_type": target_type, "target_id": target_id, "action": action, "reviewer": reviewer}
    state["actions"] = [entry, *state["actions"]][:50]
    _save_console_state(state)
    from layer4_evidence import EvidenceStore
    EvidenceStore(AUDIT_DB).save_action(target_id, f"demo_{action}", {"status": "completed_locally", "action": action, "mode": "demo"})
    logger.info("console_demo_action", **entry)
    return {"status": "completed_locally", "mode": "demo", "action": action, "target_id": target_id}

@app.post("/api/demo/reset", dependencies=[Depends(console_user)])
async def reset_demo_console(reviewer: str = Depends(console_user)):
    """Reset only local visual demo state; evidence audit records remain preserved."""
    _save_console_state({"transactions": {}, "mandates": {}, "actions": []})
    logger.info("console_demo_reset", reviewer=reviewer)
    return {"status": "reset"}

@app.post("/api/reviews/{transaction_id}", dependencies=[Depends(console_user)])
async def reviewer_decision(transaction_id: str, body: dict, reviewer: str = Depends(console_user)):
    decision = body.get("decision")
    if decision not in {"approve_action", "override_to_clear", "request_more_evidence"}:
        raise HTTPException(400, "Unsupported reviewer decision")
    from layer4_evidence import EvidenceStore, queue_risk_notification
    store = EvidenceStore(AUDIT_DB)
    store.save_review(transaction_id, reviewer, decision, str(body.get("note", "")))
    packet = store.fetch_packet(transaction_id) or next((row["packet"] for row in console_rows() if row["id"] == transaction_id), None)
    notification = queue_risk_notification(packet, store) if packet else None
    logger.info("reviewer_decision_recorded", transaction_id=transaction_id, reviewer=reviewer, decision=decision)
    return {"status": "recorded", "decision": decision, "notification": notification, "reviews": store.reviews(transaction_id)}

@app.get("/api/reviews/{transaction_id}", dependencies=[Depends(console_user)])
async def reviewer_history(transaction_id: str):
    from layer4_evidence import EvidenceStore
    return {"reviews": EvidenceStore(AUDIT_DB).reviews(transaction_id)}

@app.post("/api/disputes/{dispute_id}/{action}", dependencies=[Depends(console_user)])
async def dispute_action(dispute_id: str, action: str, body: dict):
    if action not in {"accept", "contest"}: raise HTTPException(400, "Unsupported dispute action")
    from integrations.razorpay import get_client
    try:
        client = get_client()
        result = client.accept_dispute(dispute_id) if action == "accept" else client.contest_dispute(dispute_id, body.get("evidence", {}), submit=bool(body.get("submit")))
        return {"status":"submitted_to_razorpay", "result":result}
    except Exception as exc:
        logger.warning("console_dispute_action_failed", dispute_id=dispute_id, action=action, error_type=type(exc).__name__)
        raise HTTPException(503, f"Razorpay test-mode action was not completed: {exc}")

@app.post("/api/mandates/{umn}/{action}", dependencies=[Depends(console_user)])
async def mandate_action(umn: str, action: str, body: dict):
    if action not in {"pause", "resume", "revoke"}: raise HTTPException(400, "Unsupported mandate action")
    from integrations.razorpay import get_client
    try:
        client = get_client(); payload = body.get("payload", {})
        result = client.pause_mandate(umn, payload) if action == "pause" else client.resume_mandate(umn, payload) if action == "resume" else client.update_or_revoke_mandate(umn, {**payload, "action":"revoke"})
        return {"status":"submitted_to_razorpay", "result":result}
    except Exception as exc:
        logger.warning("console_mandate_action_failed", umn=umn, action=action, error_type=type(exc).__name__)
        raise HTTPException(503, f"Razorpay test-mode action was not completed: {exc}")

app.mount("/", StaticFiles(directory=ROOT / "console", html=True), name="console")
