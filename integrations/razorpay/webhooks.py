"""Razorpay webhook signature validation; never trust a parsed body alone."""
from __future__ import annotations

import hashlib
import hmac
import os


class WebhookVerificationError(ValueError):
    pass


def verify_webhook_signature(raw_body: bytes, signature: str | None, secret: str | None = None) -> None:
    signing_secret = secret or os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not signing_secret:
        raise WebhookVerificationError("RAZORPAY_WEBHOOK_SECRET is not configured")
    if not signature:
        raise WebhookVerificationError("Missing Razorpay webhook signature")
    expected = hmac.new(signing_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise WebhookVerificationError("Invalid Razorpay webhook signature")
