"""Small SQLite audit store for evidence packets and action outcomes."""
from __future__ import annotations

import json
import sqlite3
import hashlib
import hmac
import os
from pathlib import Path
from typing import Any

class EvidenceStore:
    """Tenant-scoped SQLite demo store with a tamper-evident event ledger."""
    def __init__(self, path: str | Path = "results/evidence_audit.sqlite", merchant_id: str | None = None):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        self.merchant_id = merchant_id or os.getenv("MERCHANT_ID", "demo-merchant")
        if not self.merchant_id.strip():
            raise ValueError("merchant_id must not be empty")
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS evidence_packets (transaction_id TEXT PRIMARY KEY, generated_at TEXT NOT NULL, liability TEXT NOT NULL, packet_json TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS action_audit (id INTEGER PRIMARY KEY, transaction_id TEXT, action TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS reviewer_decisions (id INTEGER PRIMARY KEY, transaction_id TEXT NOT NULL, reviewer TEXT NOT NULL, decision TEXT NOT NULL, note TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY, transaction_id TEXT NOT NULL, recipient TEXT NOT NULL, channel TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.execute("CREATE TABLE IF NOT EXISTS audit_ledger (id INTEGER PRIMARY KEY, merchant_id TEXT NOT NULL, event_type TEXT NOT NULL, event_json TEXT NOT NULL, previous_hash TEXT, event_hash TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    def _connect(self): return sqlite3.connect(self.path)
    def _append_ledger(self, event_type: str, payload: dict[str, Any]) -> None:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        with self._connect() as conn:
            previous = conn.execute("SELECT event_hash FROM audit_ledger WHERE merchant_id=? ORDER BY id DESC LIMIT 1", (self.merchant_id,)).fetchone()
            previous_hash = previous[0] if previous else ""
            material = f"{self.merchant_id}|{event_type}|{previous_hash}|{canonical}".encode()
            secret = os.getenv("AUDIT_HMAC_SECRET")
            event_hash = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest() if secret else hashlib.sha256(material).hexdigest()
            conn.execute("INSERT INTO audit_ledger (merchant_id, event_type, event_json, previous_hash, event_hash) VALUES (?, ?, ?, ?, ?)", (self.merchant_id, event_type, canonical, previous_hash or None, event_hash))
    def verify_ledger(self) -> bool:
        with self._connect() as conn:
            rows = conn.execute("SELECT event_type, event_json, previous_hash, event_hash FROM audit_ledger WHERE merchant_id=? ORDER BY id", (self.merchant_id,)).fetchall()
        previous_hash = ""
        for event_type, event_json, recorded_previous, recorded_hash in rows:
            if (recorded_previous or "") != previous_hash:
                return False
            material = f"{self.merchant_id}|{event_type}|{previous_hash}|{event_json}".encode()
            secret = os.getenv("AUDIT_HMAC_SECRET")
            expected = hmac.new(secret.encode(), material, hashlib.sha256).hexdigest() if secret else hashlib.sha256(material).hexdigest()
            if not hmac.compare_digest(expected, recorded_hash):
                return False
            previous_hash = recorded_hash
        return True
    def save_packet(self, packet: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO evidence_packets VALUES (?, ?, ?, ?)", (packet["transaction"].get("transaction_id", "unknown"), packet["generated_at"], packet["liability_determination"], json.dumps(packet, default=str)))
        self._append_ledger("evidence_packet_saved", packet)
    def save_action(self, transaction_id: str, action: str, result: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO action_audit (transaction_id, action, status, result_json) VALUES (?, ?, ?, ?)", (transaction_id, action, result.get("status", "unknown"), json.dumps(result, default=str)))
        self._append_ledger("action_saved", {"transaction_id": transaction_id, "action": action, "result": result})
    def fetch_packet(self, transaction_id: str) -> dict[str, Any] | None:
        with self._connect() as conn: row = conn.execute("SELECT packet_json FROM evidence_packets WHERE transaction_id=?", (transaction_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_review(self, transaction_id: str, reviewer: str, decision: str, note: str = "") -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO reviewer_decisions (transaction_id, reviewer, decision, note) VALUES (?, ?, ?, ?)", (transaction_id, reviewer, decision, note))
        self._append_ledger("review_saved", {"transaction_id": transaction_id, "reviewer": reviewer, "decision": decision, "note": note})
    def save_notification(self, transaction_id: str, recipient: str, channel: str, message: str, status: str = "queued") -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO notifications (transaction_id, recipient, channel, message, status) VALUES (?, ?, ?, ?, ?)", (transaction_id, recipient, channel, message, status))
        self._append_ledger("notification_saved", {"transaction_id": transaction_id, "recipient": recipient, "channel": channel, "message": message, "status": status})
    def reviews(self, transaction_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn: rows = conn.execute("SELECT reviewer, decision, note, created_at FROM reviewer_decisions WHERE transaction_id=? ORDER BY id DESC", (transaction_id,)).fetchall()
        return [{"reviewer": r[0], "decision": r[1], "note": r[2], "created_at": r[3]} for r in rows]
    def purge_older_than(self, days: int) -> dict[str, int]:
        """Purge local demo audit records older than a validated retention window."""
        if days < 1:
            raise ValueError("Retention must be at least one day")
        cutoff = f"-{days} days"
        deleted: dict[str, int] = {}
        with self._connect() as conn:
            for table, timestamp in (("evidence_packets", "generated_at"), ("action_audit", "created_at"), ("reviewer_decisions", "created_at"), ("notifications", "created_at")):
                cursor = conn.execute(f"DELETE FROM {table} WHERE julianday({timestamp}) < julianday('now', ?)", (cutoff,))
                deleted[table] = cursor.rowcount
        return deleted
