"""Small, auditable merchant policy store for Layer 2/3 thresholds."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


DEFAULT_POLICY = {
    "version": 1,
    "i1_threshold": 0.35,
    "layer2_attack_threshold": 0.50,
    "layer2_suspicious_threshold": 0.25,
}


class MerchantPolicyStore:
    def __init__(self, path: str | Path = "results/merchant_policies.json") -> None:
        self.path = Path(path)
        self._lock = Lock()

    def get(self, merchant_id: str) -> dict[str, Any]:
        with self._lock:
            policies = json.loads(self.path.read_text()) if self.path.exists() else {}
        return {**DEFAULT_POLICY, **policies.get(merchant_id, {})}

    def update(self, merchant_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        allowed = set(DEFAULT_POLICY) - {"version"}
        if set(changes) - allowed:
            raise ValueError("Unsupported policy field")
        policy = self.get(merchant_id)
        for name, value in changes.items():
            value = float(value)
            if not 0 < value < 1:
                raise ValueError(f"{name} must be between 0 and 1")
            policy[name] = value
        policy["version"] = int(policy.get("version", 0)) + 1
        with self._lock:
            policies = json.loads(self.path.read_text()) if self.path.exists() else {}
            policies[merchant_id] = policy
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(policies, indent=2, sort_keys=True) + "\n")
        return policy
