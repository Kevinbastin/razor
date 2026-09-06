import main
import json


def test_console_demo_state_changes_rows_and_can_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CONSOLE_STATE_PATH", tmp_path / "console_state.json")
    original = main.console_rows()
    mandate_id = original[0]["packet"]["mandate_snapshot"]["mandate_id"]
    main._save_console_state({
        "transactions": {original[0]["id"]: {"status": "pending review", "timestamp": "Updated just now"}},
        "mandates": {mandate_id: {"state": "paused"}},
        "actions": [],
    })
    changed = main.console_rows()[0]
    assert changed["status"] == "pending review"
    assert changed["packet"]["mandate_snapshot"]["lifecycle_state"] == "paused"
    main._save_console_state({"transactions": {}, "mandates": {}, "actions": []})
    assert main.console_rows()[0]["status"] == original[0]["status"]


def test_console_json_boundary_converts_nonfinite_values_to_null():
    payload = main._json_safe({"nested": [float("nan"), float("inf"), -float("inf"), 1.5]})
    assert payload == {"nested": [None, None, None, 1.5]}
    json.dumps(payload, allow_nan=False)
