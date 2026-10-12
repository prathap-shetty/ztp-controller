import hashlib
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.errors import ZtpError
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.models.contracts import DeviceIntent, ObservedDevice
from app.settings import Settings
from app.vendors.cisco_iosxr import CiscoIosxrAdapter


def xr_intent():
    data = LocalYamlInventoryProvider(Path("inventory/devices.iosxr.example.yaml")).list_entries()[
        0
    ]
    payload = data.model_dump()
    payload["ztp_enabled"] = True
    return DeviceIntent.model_validate(payload)


def xr_observed():
    return ObservedDevice(
        serial_number="REPLACE_WITH_CHASSIS_SERIAL",
        model="IOSXRV9000",
        vendor="cisco",
        platform="iosxr",
        current_version="24.2.2",
        management_mac="52:54:00:11:22:33",
    )


def xr_settings():
    return Settings(
        database_url="postgresql://unused",
        inventory_provider="local-yaml",
        lite_mode=True,
        xr_enabled=True,
        execution_mode="configuration-only",
        admin_password="TestPassword123",
    )


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("xr_bootstrap", "poap/cisco/iosxr.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_xr_manifest_and_render():
    manifest, config = CiscoIosxrAdapter().prepare(xr_intent(), xr_observed(), xr_settings())
    assert manifest.actions == ("apply-config",)
    assert not manifest.upgrade_required
    assert manifest.validation_policy == "manual"
    assert "target" not in manifest.model_dump()
    assert "interface MgmtEth0/RP0/CPU0/0" in config
    assert "ipv4 address 10.10.10.100 255.255.255.0" in config
    assert "secret 0 TestPassword123" in config
    assert "mgmt0" not in config
    with pytest.raises(ZtpError, match="Enable IOS XR"):
        CiscoIosxrAdapter().prepare(
            xr_intent(), xr_observed(), xr_settings().model_copy(update={"xr_enabled": False})
        )


def test_xr_rejects_software_and_nxos_management():
    payload = xr_intent().model_dump()
    payload["software"] = {
        "target_version": "10.5(4)",
        "image_name": "nxos.bin",
        "image_checksum": "a" * 64,
    }
    with pytest.raises(ValidationError):
        DeviceIntent.model_validate(payload)
    payload["software"] = None
    payload["management"].update(interface="mgmt0", vrf="management")
    with pytest.raises(ValidationError):
        DeviceIntent.model_validate(payload)


def test_xr_discovery():
    module = load_bootstrap()
    outputs = {
        "show inventory": (
            'NAME: "Rack 0", DESCR: "XRv9000"\nPID: IOSXRV9000, VID: V01, SN: SERIAL01'
        ),
        "show version": "Cisco IOS XR Software, Version 24.2.2",
        "show interfaces brief": "MgmtEth0/RP0/CPU0/0 up up",
        "show interfaces MgmtEth0/RP0/CPU0/0": (
            "Hardware is Management Ethernet, address is 5254.0011.2233"
        ),
    }

    class Helper:
        def xrcmd(self, request):
            return {"status": "success", "output": [outputs[request["exec_cmd"]]]}

    observed, interface = module.discover(Helper())
    assert observed["serial_number"] == "SERIAL01"
    assert observed["management_mac"] == "52:54:00:11:22:33"
    assert interface == "MgmtEth0/RP0/CPU0/0"
    outputs["show inventory"] = 'NAME: "Slot 0", DESCR: "RP"\nPID: RP, VID: V01, SN: OTHER'
    with pytest.raises(module.BootstrapError, match="chassis"):
        module.discover(Helper())


@pytest.mark.parametrize("scenario", ["success", "checksum", "apply-failed", "callback-failed"])
def test_xr_bootstrap_apply(monkeypatch, tmp_path, scenario):
    module = load_bootstrap()
    manifest, config = CiscoIosxrAdapter().prepare(xr_intent(), xr_observed(), xr_settings())
    registration = {
        "manifest": manifest.model_dump(mode="json"),
        "provisioning_id": str(uuid4()),
        "status_token": "test-token",
    }
    events, applied = [], []

    def request(path, data=None, token=None, limit=None):
        if path.endswith("/register"):
            return json.dumps(registration).encode()
        if "/config/" in path:
            return config.encode() if scenario != "checksum" else b"corrupted"
        events.append(data["event"])
        if data["event"] == "CONFIG_COMMITTED" and scenario == "callback-failed":
            raise RuntimeError("unreachable")
        return b"{}"

    class Helper:
        def xrapply(self, filename, reason):
            applied.append(Path(filename).read_bytes())
            return {"status": "error" if scenario == "apply-failed" else "success"}

    monkeypatch.setattr(module, "request", request)
    monkeypatch.setattr(module, "CHECKPOINT_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    if scenario in {"checksum", "apply-failed"}:
        with pytest.raises(module.BootstrapError):
            module.apply(Helper(), xr_observed().model_dump(), manifest.management.interface)
        assert len(applied) == (0 if scenario == "checksum" else 1)
        if scenario == "apply-failed":
            assert events == ["CONFIG_APPLYING", "FAILED"]
        return
    assert module.apply(Helper(), xr_observed().model_dump(), manifest.management.interface) == 0
    assert len(applied) == 1
    assert hashlib.sha256(applied[0]).hexdigest() == manifest.config.sha256
    # A committed checkpoint suppresses reapplication even if the callback failed.
    assert module.apply(Helper(), xr_observed().model_dump(), manifest.management.interface) == 0
    assert len(applied) == 1
    checkpoint = tmp_path / (registration["provisioning_id"] + ".json")
    checkpoint.write_text(json.dumps({"sha256": manifest.config.sha256, "state": "applying"}))
    with pytest.raises(module.BootstrapError, match="uncertain"):
        module.apply(Helper(), xr_observed().model_dump(), manifest.management.interface)
    assert len(applied) == 1


def test_netbox_xr_without_image(records, netbox):
    device = records["device"]
    device["platform"]["slug"] = "cisco_iosxr"
    device["config_context"]["ztp"] = {
        "management": {"gateway": "192.0.2.1", "interface": "MgmtEth0/RP0/CPU0/0", "vrf": "default"}
    }
    records["ip"]["assigned_object"]["name"] = "MgmtEth0/RP0/CPU0/0"
    # Use the fixture's existing subnet for the gateway.
    import ipaddress

    device["config_context"]["ztp"]["management"]["gateway"] = str(
        ipaddress.ip_interface(records["ip"]["address"]).network.network_address + 1
    )
    intent = netbox[0].get_device_intent("1")
    assert intent.device.platform == "iosxr"
    assert intent.software is None
    device["config_context"]["ztp"]["image"] = {}
    with pytest.raises(ZtpError, match="software intent"):
        netbox[0].get_device_intent("1")
