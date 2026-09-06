from __future__ import annotations

import pandas as pd
import pytest

from layer4_evidence.store import EvidenceStore
from platform_security import SlidingWindowRateLimiter
from scripts.check_feature_drift import assess_drift
from integrations.razorpay.webhooks import WebhookVerificationError, verify_webhook_signature
import hashlib
import hmac


def test_sliding_window_limiter_reopens_after_window():
    limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
    assert limiter.allow("merchant", now=0)[0]
    assert limiter.allow("merchant", now=1)[0]
    allowed, retry = limiter.allow("merchant", now=2)
    assert not allowed and retry > 0
    assert limiter.allow("merchant", now=61)[0]


def test_audit_retention_rejects_invalid_window(tmp_path):
    store = EvidenceStore(tmp_path / "audit.sqlite")
    with pytest.raises(ValueError):
        store.purge_older_than(0)
    result = store.purge_older_than(90)
    assert set(result) == {"evidence_packets", "action_audit", "reviewer_decisions", "notifications"}


def test_drift_gate_flags_material_distribution_shift():
    baseline = pd.DataFrame({"amount": list(range(1, 101)), "constant": [1] * 100})
    same = pd.DataFrame({"amount": list(range(1, 101)), "constant": [1] * 100})
    shifted = pd.DataFrame({"amount": list(range(1001, 1101)), "constant": [1] * 100})
    assert assess_drift(baseline, same)["verdict"] == "pass"
    assert assess_drift(baseline, shifted)["verdict"] == "fail"


def test_audit_ledger_is_tamper_evident(tmp_path, monkeypatch):
    monkeypatch.setenv("AUDIT_HMAC_SECRET", "test-audit-key")
    store = EvidenceStore(tmp_path / "audit.sqlite", merchant_id="merchant-a")
    store.save_review("txn-1", "analyst", "request_more_evidence", "review")
    assert store.verify_ledger()
    import sqlite3
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE audit_ledger SET event_json='tampered' WHERE id=1")
    assert not store.verify_ledger()


def test_webhook_signature_verification_is_constant_time_compatible():
    body, secret = b'{"event":"payment.dispute.created"}', "webhook-secret"
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    verify_webhook_signature(body, signature, secret)
    with pytest.raises(WebhookVerificationError):
        verify_webhook_signature(body, "not-valid", secret)
