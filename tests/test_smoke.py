"""
Smoke tests — verify the project scaffolding works.
"""

import importlib


def test_all_packages_importable():
    """Every layer package should be importable without errors."""
    packages = [
        "simulator",
        "layer1_verifier",
        "layer2_detector",
        "layer3_intent",
        "layer4_evidence",
        "integrations",
        "integrations.razorpay",
    ]
    for pkg in packages:
        mod = importlib.import_module(pkg)
        assert mod is not None, f"Failed to import {pkg}"


def test_logging_config():
    """Structured logging should initialize without errors."""
    from logging_config import setup_logging
    setup_logging("DEBUG")  # Should not raise


def test_fastapi_app_exists():
    """FastAPI app should be importable."""
    from main import app
    assert app.title == "Agent Transaction Risk Layer"
