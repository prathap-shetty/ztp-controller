from pathlib import Path

import pytest

from app.errors import InventoryDenied
from app.models.contracts import ObservedDevice
from app.services.authorization import authorize_identity
from app.services.hashing import stable_hash
from app.vendors.cisco_nxos import CiscoNxosAdapter


@pytest.mark.parametrize(
    "change",
    [
        {"status": "active"},
        {"status": "offline"},
        {"vendor": "arista"},
        {"platform": "eos"},
        {"model": "N9K-UNKNOWN"},
        {"serial_number": "DIFFERENT"},
    ],
)
def test_identity_policy(records, netbox, change):
    device = netbox[0].get_device_by_serial("FDO12345678").model_copy(update=change)
    with pytest.raises(InventoryDenied):
        authorize_identity(device, ObservedDevice(**records["observed"]))


@pytest.mark.parametrize("version,upgrade", [("10.3(5)", True), ("10.4(3)F", False)])
def test_manifest_is_stable_and_never_executable(records, netbox, version, upgrade):
    adapter = CiscoNxosAdapter(Path("tests/fixtures/profiles.json"))
    intent = netbox[0].get_device_intent("1")
    observed = ObservedDevice(**{**records["observed"], "current_version": version})
    first = adapter.build_manifest(intent, observed).model_dump(mode="json")
    second = adapter.build_manifest(intent, observed).model_dump(mode="json")
    assert stable_hash(first) == stable_hash(second)
    assert first["upgrade_required"] is upgrade
    assert first["execution_enabled"] is False
    assert first["actions"] == []
    assert "url" not in first


@pytest.mark.parametrize(
    "change",
    [
        {"current_version": "99.1(1)"},
        {"model": "N9K-UNKNOWN"},
    ],
)
def test_unapproved_path_rejected(records, netbox, change):
    adapter = CiscoNxosAdapter(Path("tests/fixtures/profiles.json"))
    with pytest.raises(InventoryDenied):
        adapter.build_manifest(
            netbox[0].get_device_intent("1"), ObservedDevice(**{**records["observed"], **change})
        )


def test_empty_production_catalog_denies(records, netbox):
    with pytest.raises(InventoryDenied):
        CiscoNxosAdapter(Path("catalog/profiles.json")).build_manifest(
            netbox[0].get_device_intent("1"),
            ObservedDevice(**records["observed"]),
        )
