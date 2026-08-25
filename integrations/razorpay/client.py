"""
Razorpay API Client

Wraps the razorpay Python SDK with structured logging and
environment-variable-based configuration. No hardcoded secrets.
"""

import os
import razorpay
import structlog

logger = structlog.get_logger(__name__)


def get_client() -> razorpay.Client:
    """
    Create a Razorpay client from environment variables.

    Raises:
        ValueError: If RAZORPAY_KEY_ID or RAZORPAY_KEY_SECRET is not set.
    """
    key_id = os.environ.get("RAZORPAY_KEY_ID")
    key_secret = os.environ.get("RAZORPAY_KEY_SECRET")

    if not key_id or not key_secret:
        logger.error(
            "razorpay_credentials_missing",
            key_id_set=bool(key_id),
            key_secret_set=bool(key_secret),
        )
        raise ValueError(
            "RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set. "
            "See .env.example for reference."
        )

    client = razorpay.Client(auth=(key_id, key_secret))
    logger.info("razorpay_client_initialized", key_id_prefix=key_id[:12] + "...")
    return client
