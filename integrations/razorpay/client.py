"""Resilient, deliberately thin Razorpay test-mode API client.

TPAP Pro mandate traffic is bank-routed; set ``RAZORPAY_TPAP_BASE_URL`` to the
host Razorpay provisions for the test account. Disputes use the standard host.
"""
from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from threading import Lock
from typing import Any

import requests
import structlog

logger = structlog.get_logger(__name__)


class RazorpayClientError(RuntimeError): pass
class CircuitOpenError(RazorpayClientError): pass


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after_seconds: float = 30.0
    failures: int = 0
    opened_at: float | None = None
    def allow(self) -> bool:
        if self.opened_at is None: return True
        if time.monotonic() - self.opened_at >= self.reset_after_seconds:
            self.failures, self.opened_at = 0, None
            return True
        return False
    def success(self) -> None: self.failures, self.opened_at = 0, None
    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold: self.opened_at = time.monotonic()


class RazorpayClient:
    """HTTP wrapper with local idempotency, retry and circuit protection."""
    TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}
    def __init__(self, key_id: str | None = None, key_secret: str | None = None,
                 api_base_url: str | None = None, tpap_base_url: str | None = None,
                 session: requests.Session | None = None, max_retries: int = 3):
        self.key_id, self.key_secret = key_id or os.getenv("RAZORPAY_KEY_ID"), key_secret or os.getenv("RAZORPAY_KEY_SECRET")
        if not self.key_id or not self.key_secret: raise ValueError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set.")
        self.api_base_url = (api_base_url or os.getenv("RAZORPAY_API_BASE_URL", "https://api.razorpay.com")).rstrip("/")
        self.tpap_base_url = (tpap_base_url or os.getenv("RAZORPAY_TPAP_BASE_URL", self.api_base_url)).rstrip("/")
        self.session, self.max_retries, self.breaker = session or requests.Session(), max_retries, CircuitBreaker()
        self.session.auth = (self.key_id, self.key_secret)
        self._idempotent_responses: dict[str, dict] = {}; self._lock = Lock()

    def _request(self, method: str, path: str, *, payload: dict | None = None, params: dict | None = None,
                 files: Any = None, tpap: bool = False, idempotency_key: str | None = None) -> dict:
        changing = method.upper() in {"POST", "PATCH", "PUT", "DELETE"}
        if changing:
            idempotency_key = idempotency_key or str(uuid.uuid4())
            with self._lock:
                if idempotency_key in self._idempotent_responses: return self._idempotent_responses[idempotency_key]
        if not self.breaker.allow(): raise CircuitOpenError("Razorpay circuit is open; action was not attempted")
        headers = {"X-Request-Id": str(uuid.uuid4())}
        if files is None: headers["Content-Type"] = "application/json"
        if changing: headers["X-Idempotency-Key"] = idempotency_key
        url = f"{self.tpap_base_url if tpap else self.api_base_url}{path}"
        for attempt in range(self.max_retries + 1):
            try:
                request_kwargs = {"params": params, "headers": headers, "timeout": (3.05, 20)}
                if files is None: request_kwargs["json"] = payload
                else: request_kwargs.update({"data": payload, "files": files})
                response = self.session.request(method, url, **request_kwargs)
                if response.status_code in self.TRANSIENT_STATUS_CODES and attempt < self.max_retries:
                    time.sleep(.25 * 2 ** attempt); continue
                if response.status_code >= 400:
                    if response.status_code >= 500: self.breaker.failure()
                    raise RazorpayClientError(f"Razorpay {method} {path} failed: {response.status_code} {response.text[:500]}")
                body = response.json() if response.content else {}
                self.breaker.success()
                if changing:
                    with self._lock: self._idempotent_responses[idempotency_key] = body
                logger.info("razorpay_request_succeeded", method=method, path=path, status=response.status_code)
                return body
            except requests.RequestException as exc:
                if attempt < self.max_retries: time.sleep(.25 * 2 ** attempt); continue
                self.breaker.failure(); raise RazorpayClientError(f"Razorpay network failure for {method} {path}: {type(exc).__name__}") from exc
        raise AssertionError("unreachable")

    def create_mandate(self, payload: dict, *, idempotency_key: str | None = None) -> dict: return self._request("POST", "/v1/upi/tpap/mandates", payload=payload, tpap=True, idempotency_key=idempotency_key)
    def fetch_mandate(self, umn: str) -> dict: return self._request("GET", f"/v1/upi/tpap/mandates/{umn}", tpap=True)
    def update_or_revoke_mandate(self, umn: str, payload: dict, *, idempotency_key: str | None = None) -> dict: return self._request("PATCH", f"/v1/upi/tpap/mandates/{umn}", payload=payload, tpap=True, idempotency_key=idempotency_key)
    def pause_mandate(self, umn: str, payload: dict, *, idempotency_key: str | None = None) -> dict: return self.update_or_revoke_mandate(umn, {**payload, "action": "pause"}, idempotency_key=idempotency_key)
    def resume_mandate(self, umn: str, payload: dict, *, idempotency_key: str | None = None) -> dict: return self.update_or_revoke_mandate(umn, {**payload, "action": "unpause"}, idempotency_key=idempotency_key)
    def approve_mandate(self, umn: str, payload: dict, *, idempotency_key: str | None = None) -> dict: return self._request("POST", f"/v1/mandates/{umn}", payload={**payload, "action": "approve"}, tpap=True, idempotency_key=idempotency_key)
    def reject_mandate(self, umn: str, payload: dict, *, idempotency_key: str | None = None) -> dict: return self._request("POST", f"/v1/mandates/{umn}", payload={**payload, "action": "reject"}, tpap=True, idempotency_key=idempotency_key)
    def list_disputes(self, **params: Any) -> dict: return self._request("GET", "/v1/disputes", params=params or None)
    def fetch_dispute(self, dispute_id: str, *, expand: list[str] | None = None) -> dict: return self._request("GET", f"/v1/disputes/{dispute_id}", params={"expand[]": expand} if expand else None)
    def accept_dispute(self, dispute_id: str, *, idempotency_key: str | None = None) -> dict: return self._request("PATCH", f"/v1/disputes/{dispute_id}/accept", payload={}, idempotency_key=idempotency_key)
    def contest_dispute(self, dispute_id: str, evidence: dict, *, submit: bool = False, idempotency_key: str | None = None) -> dict: return self._request("PATCH", f"/v1/disputes/{dispute_id}/contest", payload={**evidence, "action": "submit" if submit else "draft"}, idempotency_key=idempotency_key)
    def upload_dispute_document(self, file_path: str, *, idempotency_key: str | None = None) -> dict:
        with open(file_path, "rb") as document:
            return self._request("POST", "/v1/documents", payload={"purpose": "dispute_evidence"}, files={"file": document}, idempotency_key=idempotency_key)


def get_client() -> RazorpayClient: return RazorpayClient()
