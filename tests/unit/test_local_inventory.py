import copy
from pathlib import Path

import pytest
import yaml

from app.errors import InvalidIntent, InventoryDenied, InventoryUnavailable
from app.inventory.factory import build_provider
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.settings import Settings


@pytest.fixture
def local_document(netbox):
    return {
        "schema_version": 1,
        "devices": [netbox[0].get_device_intent("1").model_dump(mode="json")],
    }


def save(tmp_path, document):
    path = tmp_path / "devices.yaml"
    path.write_text(yaml.safe_dump(document))
    return LocalYamlInventoryProvider(path)


def test_local_without_netbox_and_live_revocation(tmp_path, local_document, monkeypatch):
    provider = save(tmp_path, local_document)

    def no_netbox(*a, **kw):
        raise AssertionError("Must not construct a NetBox provider")

    monkeypatch.setattr("app.inventory.factory.NetBoxInventoryProvider", no_netbox)
    settings = Settings(
        database_url="postgresql+psycopg://unused",
        inventory_provider="local-yaml",
        local_inventory_path=provider.path,
    )
    provider = build_provider(settings)
    provider.check_ready()
    assert provider.get_device_by_serial("fdo12345678").id == "1"
    assert str(provider.get_management_ip("1").address) == "192.0.2.10/24"
    local_document["devices"][0]["ztp_enabled"] = False
    save(tmp_path, local_document)
    with pytest.raises(InventoryDenied):
        provider.get_device_intent("1")
    with pytest.raises(InventoryDenied):
        provider.get_device_by_serial("UNKNOWN")
    with pytest.raises(NotImplementedError):
        provider.update_device_status("1", "active")
    provider.path.unlink()
    with pytest.raises(InventoryUnavailable):
        provider.check_ready()


@pytest.mark.parametrize(
    "fault",
    ["duplicate-id", "duplicate-serial", "quoted-bool", "wrong-interface", "bad-checksum", "extra"],
)
def test_invalid_local_inventory_fails_closed(tmp_path, local_document, fault):
    entry = local_document["devices"][0]
    if fault.startswith("duplicate"):
        other = copy.deepcopy(entry)
        if fault == "duplicate-id":
            other["device"]["serial_number"] = "ANOTHER"
        else:
            other["device"]["id"] = "another"
            other["device"]["serial_number"] = entry["device"]["serial_number"].lower()
        local_document["devices"].append(other)
    elif fault == "quoted-bool":
        entry["ztp_enabled"] = "true"
    elif fault == "wrong-interface":
        entry["management"]["interface"] = "Ethernet1/1"
    elif fault == "bad-checksum":
        entry["software"]["image_checksum"] = "invalid"
    else:
        entry["unrecognized"] = "value"
    with pytest.raises(InvalidIntent):
        save(tmp_path, local_document).check_ready()


@pytest.mark.parametrize(
    "text",
    [
        "schema_version: 1\nschema_version: 1\ndevices: []",
        "schema_version: 1\ndevices: &items [*items]",
        "!!python/object/apply:os.system [echo unsafe]",
        "schema_version: [",
    ],
)
def test_unsafe_or_ambiguous_yaml(tmp_path, text):
    path = tmp_path / "devices.yaml"
    path.write_text(text)
    with pytest.raises(InvalidIntent):
        LocalYamlInventoryProvider(path).check_ready()


def test_shipped_inventory_examples():
    for name in ["devices.yaml", "devices.example.yaml"]:
        LocalYamlInventoryProvider(Path("inventory") / name).check_ready()


def test_netbox_still_requires_credentials():
    with pytest.raises(ValueError):
        Settings(database_url="postgresql+psycopg://unused", netbox_url="https://netbox.test")
