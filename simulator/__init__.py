"""
Agent Transaction Risk Layer — Simulator

Synthetic data generation and attack scenario simulation.
Generates mandate histories, agent sessions, and injection-attack
scenarios for training and evaluation.

Usage:
    python -m simulator.run [--output-dir simulator/data] [--seed 42]

Modules:
    config.py               — All tunable parameters (versioned, frozen dataclass)
    mandate_generator.py    — Generates ~500 synthetic mandates
    transaction_generator.py — Generates ~50K transactions + session events
    run.py                  — Orchestrator: generate, write, split
"""

from simulator.config import SimulatorConfig

__all__ = ["SimulatorConfig"]
