"""Strictly grounded LLM narrative generation for evidence packets."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

SYSTEM_INSTRUCTION = """You write a concise dispute-evidence narrative. Use ONLY facts explicitly present in the JSON packet below. Do not infer intent, identity, causation, delivery, account status, or API results. Do not add facts, estimates, legal conclusions, or recommendations. State the packet's deterministic liability determination exactly; do not change it. If a fact is absent, omit it."""


def build_prompt(packet: dict[str, Any]) -> str:
    return f"{SYSTEM_INSTRUCTION}\n\nEVIDENCE PACKET (sole source of truth):\n{json.dumps(packet, sort_keys=True, default=str)}\n\nWrite 2-4 plain-English paragraphs for a dispute reviewer."


def generate_narrative(packet: dict[str, Any]) -> dict[str, Any]:
    """Call Responses API when configured; otherwise return an explicit safe fallback."""
    prompt = build_prompt(packet)
    api_key, model = os.getenv("OPENAI_API_KEY"), os.getenv("OPENAI_NARRATIVE_MODEL")
    if not api_key or not model:
        return {"status": "not_generated", "reason": "OPENAI_API_KEY and OPENAI_NARRATIVE_MODEL are required", "prompt": prompt, "narrative": None}
    response = httpx.post("https://api.openai.com/v1/responses", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                          json={"model": model, "input": prompt, "store": False}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    text = payload.get("output_text")
    if not text:
        raise RuntimeError("OpenAI response contained no output_text")
    logger.info("evidence_narrative_generated", response_id=payload.get("id"), model=model)
    return {"status": "generated", "provider": "openai", "model": model, "response_id": payload.get("id"), "prompt": prompt, "narrative": text}
