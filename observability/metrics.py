"""Thread-safe in-memory decision counters; exportable for a future console."""
from collections import Counter, deque
from threading import Lock

class DecisionMetrics:
    def __init__(self, window_size: int = 1000):
        self.window_size, self._events, self._lock = window_size, deque(maxlen=window_size), Lock()
    def record(self, layer: str, verdict: str, *, actual_legitimate: bool | None = None) -> None:
        with self._lock: self._events.append({"layer": layer, "verdict": verdict, "actual_legitimate": actual_legitimate})
    def snapshot(self) -> dict:
        with self._lock: events = list(self._events)
        counts = Counter(f"{e['layer']}:{e['verdict']}" for e in events)
        labelled = [e for e in events if e["actual_legitimate"] is not None]
        false_positives = sum(e["actual_legitimate"] and e["verdict"] not in {"pass", "clear", "approved"} for e in labelled)
        legitimate = sum(e["actual_legitimate"] for e in labelled)
        return {"window_size": len(events), "verdict_counts": dict(counts), "rolling_false_positive_rate": false_positives / legitimate if legitimate else None}

METRICS = DecisionMetrics()
