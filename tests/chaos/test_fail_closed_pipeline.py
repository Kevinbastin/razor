from layer1_verifier.checks import verify_transaction
from layer2_detector.features import _extract_sequence_features
from layer3_intent.detector import IntentIntegrityDetector
from layer4_evidence.assembler import EvidenceAssembler

def test_malformed_authority_and_intent_are_never_approved():
    assert verify_transaction(None, {}).verdict == "fail"
    detector = IntentIntegrityDetector().fit([{"purpose":"groceries"}], [{"cart_items":["rice"], "cart_category":"grocery"}])
    assert detector.score_transaction({"cart_items":[]}, {"purpose":"groceries"}, []).verdict == "flagged"

def test_out_of_order_events_are_sequence_anomalous_and_packet_blocks():
    events = [{"step_name":"confirm_payment"}, {"step_name":"discover_mandate"}, {"step_name":"initiate_payment"}]
    assert _extract_sequence_features(events)["seq_edit_distance"] > 0
    packet = EvidenceAssembler().assemble(transaction={}, mandate_snapshot={}, layer1={"verdict":"fail", "failed_checks":["INPUT"]}, layer2={"verdict":"attack"}, layer3={"verdict":"flagged"}, session_timeline=events)
    assert packet.transaction_disposition == "blocked"
