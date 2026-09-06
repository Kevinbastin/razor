from merchant_policy import MerchantPolicyStore
import pytest


def test_merchant_policy_is_versioned_and_validated(tmp_path):
    store = MerchantPolicyStore(tmp_path / "policies.json")
    original = store.get("merchant-a")
    updated = store.update("merchant-a", {"i1_threshold": 0.42})
    assert updated["version"] == original["version"] + 1
    assert store.get("merchant-a")["i1_threshold"] == 0.42
    with pytest.raises(ValueError):
        store.update("merchant-a", {"unknown": 0.5})
