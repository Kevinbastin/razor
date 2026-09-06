from layer4_evidence.assembler import EvidenceAssembler, determine_liability
from layer4_evidence.actions import apply_auto_response
from layer4_evidence.store import EvidenceStore
from layer4_evidence.notifications import queue_risk_notification

def test_liability_precedence_and_step_up():
    assert determine_liability({"verdict":"fail", "failed_checks":["V6"]}, {}, {"verdict":"clear"})[0] == "fraud-contest"
    packet = EvidenceAssembler().assemble(transaction={"transaction_id":"t"}, mandate_snapshot={"lifecycle_state":"active"}, layer1={"verdict":"pass", "failed_checks":[]}, layer2={"verdict":"suspicious"}, layer3={"verdict":"clear"}, session_timeline=[])
    assert packet.transaction_disposition == "pending re-authorization"
    assert apply_auto_response(packet.to_dict(), None)["status"] == "pending_reauthorization"

def test_a6_escalates_and_requests_pause():
    packet = EvidenceAssembler().assemble(transaction={"transaction_id":"t"}, mandate_snapshot={"mandate_id":"m", "lifecycle_state":"active"}, layer1={"verdict":"pass", "failed_checks":[]}, layer2={"verdict":"pass"}, layer3={"verdict":"flagged"}, session_timeline=[])
    assert packet.liability_determination == "escalate-to-provider"
    assert apply_auto_response(packet.to_dict(), None)["action"] == "pause_mandate"

def test_packet_and_action_are_durably_audited(tmp_path):
    packet = EvidenceAssembler().assemble(transaction={"transaction_id":"audit-t"}, mandate_snapshot={"lifecycle_state":"active"}, layer1={"verdict":"pass", "failed_checks":[]}, layer2={"verdict":"suspicious"}, layer3={"verdict":"clear"}, session_timeline=[], fulfillment_evidence=[{"type":"delivery_confirmation", "reference":"DEL-1"}])
    store = EvidenceStore(tmp_path / "audit.sqlite")
    store.save_packet(packet.to_dict())
    assert store.fetch_packet("audit-t")["fulfillment_evidence"][0]["reference"] == "DEL-1"
    apply_auto_response(packet.to_dict(), None, store=store)
    import sqlite3
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM action_audit").fetchone()[0] == 1

def test_reviewer_and_notification_workflow_are_audited(tmp_path):
    store = EvidenceStore(tmp_path / "review.sqlite")
    packet = {"transaction":{"transaction_id":"review-t"}, "transaction_disposition":"blocked", "liability_reason":"intent mismatch", "layer3":{"signals_triggered":["I1"]}}
    store.save_review("review-t", "analyst@example", "request_more_evidence", "Need order confirmation")
    notice = queue_risk_notification(packet, store)
    assert store.reviews("review-t")[0]["decision"] == "request_more_evidence"
    assert notice["status"] == "queued" and "I1" in notice["message"]
