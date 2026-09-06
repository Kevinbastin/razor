#!/usr/bin/env python3
"""Opt-in live Razorpay test-mode probe; requires explicit payload/IDs in env."""
import json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from integrations.razorpay import RazorpayClient

load_dotenv(ROOT / ".env")
def required(name):
    value = os.getenv(name)
    if not value: raise RuntimeError(f"Set {name} before running this live test.")
    return value

def main():
    client = RazorpayClient()
    payload = json.loads(required("RAZORPAY_TEST_MANDATE_PAYLOAD"))
    created = client.create_mandate(payload, idempotency_key="layer4-test-create-001")
    umn = created.get("umn") or required("RAZORPAY_TEST_UMN")
    print("create mandate: OK", umn)
    print("fetch mandate: OK", client.fetch_mandate(umn).get("status"))
    pause_payload = json.loads(required("RAZORPAY_TEST_PAUSE_PAYLOAD"))
    print("pause mandate: OK", client.pause_mandate(umn, pause_payload, idempotency_key="layer4-test-pause-001").get("status"))
    dispute_id = required("RAZORPAY_TEST_DISPUTE_ID")
    print("fetch dispute: OK", client.fetch_dispute(dispute_id, expand=["payment", "settlement"]).get("id"))
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as temp:
        temp.write(b"%PDF-1.4\n% Layer 4 test evidence\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")
        path = temp.name
    try:
        document = client.upload_dispute_document(path, idempotency_key="layer4-test-document-001")
        evidence = {"summary": "Layer 4 test evidence packet", "explanation_letter": [document["id"]]}
        print("contest dispute: OK", client.contest_dispute(dispute_id, evidence, submit=True, idempotency_key="layer4-test-contest-001").get("status"))
    finally: os.unlink(path)

if __name__ == "__main__": main()
