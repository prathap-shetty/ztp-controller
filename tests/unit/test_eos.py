import hashlib
import importlib.util
import ipaddress
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.errors import ZtpError
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.models.contracts import DeviceIntent, ObservedDevice, parse_manifest
from app.settings import Settings
from app.vendors.arista_eos import AristaEosAdapter
from app.vendors.cisco_iosxe import CiscoIosxeAdapter


def eos_intent():
    data = LocalYamlInventoryProvider(Path("inventory/devices.eos.example.yaml")).list_entries()[0]
    payload = data.model_dump()
    payload["ztp_enabled"] = True
    return DeviceIntent.model_validate(payload)


def eos_observed():
    return ObservedDevice(
        serial_number="REPLACE_WITH_SERIAL",
        model="VEOS-LAB",
        vendor="arista",
        platform="eos",
        current_version="4.32.1F",
        management_mac="52:54:00:77:88:99",
    )


def eos_settings():
    return Settings(
        database_url="postgresql://unused",
        inventory_provider="local-yaml",
        lite_mode=True,
        eos_enabled=True,
        execution_mode="configuration-only",
        admin_password="TestPassword123",
    )


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("eos_bootstrap", "poap/arista/eos.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_eos_manifest_and_render():
    manifest, config = AristaEosAdapter().prepare(eos_intent(), eos_observed(), eos_settings())
    assert manifest.platform == "eos"
    assert manifest.actions == ("apply-config",)
    assert not manifest.upgrade_required
    assert manifest.validation_policy == "manual"
    assert parse_manifest(manifest.model_dump(mode="json")) == manifest
    assert "interface Management1" in config
    assert "ip address 10.10.10.120/24" in config
    assert "ip route 0.0.0.0/0 10.10.10.1" in config
    assert "secret 0 TestPassword123" in config
    with pytest.raises(ZtpError, match="Enable Arista EOS"):
        AristaEosAdapter().prepare(
            eos_intent(),
            eos_observed(),
            eos_settings().model_copy(update={"eos_enabled": False, "xe_enabled": True}),
        )
    with pytest.raises(ZtpError, match="IOS XE identity"):
        CiscoIosxeAdapter().prepare(
            eos_intent(), eos_observed(), eos_settings().model_copy(update={"xe_enabled": True})
        )


def test_eos_contract_rejects_software_foreign_management_and_vendor_mismatch():
    payload = eos_intent().model_dump()
    payload["software"] = {
        "target_version": "4.32.1F",
        "image_name": "EOS.bin",
        "image_checksum": "a" * 64,
    }
    with pytest.raises(ValidationError):
        DeviceIntent.model_validate(payload)
    payload["software"] = None
    for interface, vrf in [("mgmt0", "management"), ("GigabitEthernet1", "default")]:
        payload["management"].update(interface=interface, vrf=vrf)
        with pytest.raises(ValidationError):
            DeviceIntent.model_validate(payload)
    observed = eos_observed().model_dump()
    with pytest.raises(ValidationError, match="vendor"):
        ObservedDevice.model_validate({**observed, "vendor": "cisco"})
    with pytest.raises(ValidationError, match="vendor"):
        ObservedDevice.model_validate({**observed, "platform": "nxos"})


def show_outputs(serial="", model="vEOS-lab"):
    return {
        "show version": {
            "modelName": model,
            "version": "4.32.1F",
            "serialNumber": serial,
            "systemMacAddress": "52:54:00:1a:2b:3c",
        },
        "show interfaces": {
            "interfaces": {
                "Management1": {"lineProtocolStatus": "up", "physicalAddress": "5254.0077.8899"},
                "Ethernet1": {"lineProtocolStatus": "up", "physicalAddress": "5254.0077.0001"},
            }
        },
    }


def test_eos_discovery(monkeypatch):
    module = load_bootstrap()
    data = show_outputs()
    monkeypatch.setattr(module, "show", lambda command: data[command])
    observed, interface = module.discover()
    assert observed == {
        "serial_number": "5254001A2B3C",
        "model": "VEOS-LAB",
        "vendor": "arista",
        "platform": "eos",
        "current_version": "4.32.1F",
        "management_mac": "52:54:00:77:88:99",
    }
    assert interface == "Management1"
    ObservedDevice.model_validate(observed)
    data.update(show_outputs(serial="ABC12345"))
    assert module.discover()[0]["serial_number"] == "ABC12345"
    # Physical switches never fall back from an empty serial to the MAC.
    data.update(show_outputs(model="DCS-7050SX3-48YC8"))
    with pytest.raises(module.BootstrapError, match="serial"):
        module.discover()
    data.update(show_outputs())
    data["show interfaces"]["interfaces"]["Management2"] = {
        "lineProtocolStatus": "up",
        "physicalAddress": "5254.0077.8800",
    }
    with pytest.raises(module.BootstrapError, match="unique management interface"):
        module.discover()


@pytest.mark.parametrize("scenario", ["success", "checksum", "startup-exists", "callback-failed"])
def test_eos_bootstrap_stage(monkeypatch, tmp_path, scenario):
    module = load_bootstrap()
    manifest, config = AristaEosAdapter().prepare(eos_intent(), eos_observed(), eos_settings())
    registration = {
        "manifest": manifest.model_dump(mode="json"),
        "provisioning_id": str(uuid4()),
        "status_token": "test-token",
    }
    events = []

    def request(path, data=None, token=None, limit=None):
        if path.endswith("/register"):
            return json.dumps(registration).encode()
        if "/config/" in path:
            return config.encode() if scenario != "checksum" else b"corrupted"
        events.append(data["event"])
        if data["event"] == "CONFIG_COMMITTED" and scenario == "callback-failed":
            raise RuntimeError("unreachable")
        return b"{}"

    startup = tmp_path / "startup-config"
    if scenario == "startup-exists":
        startup.write_text("hostname existing\n")
    monkeypatch.setattr(module, "request", request)
    monkeypatch.setattr(module, "FLASH", str(tmp_path))
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    observed = eos_observed().model_dump()
    if scenario in {"checksum", "startup-exists"}:
        with pytest.raises(module.BootstrapError):
            module.apply(observed, "Management1")
        if scenario == "checksum":
            assert not startup.exists() and events == []
        else:
            assert startup.read_text() == "hostname existing\n"
            assert events == ["CONFIG_APPLYING", "FAILED"]
            with pytest.raises(module.BootstrapError, match="uncertain"):
                module.apply(observed, "Management1")
        return
    assert module.apply(observed, "Management1") == 0
    assert hashlib.sha256(startup.read_bytes()).hexdigest() == manifest.config.sha256
    assert events[0] == "CONFIG_APPLYING"
    # A committed checkpoint suppresses restaging even if the callback failed.
    startup.unlink()
    assert module.apply(observed, "Management1") == 0
    assert not startup.exists()
    with pytest.raises(module.BootstrapError, match="unsupported EOS plan"):
        module.apply(observed, "Management2")


def test_netbox_eos_without_image(records, netbox):
    device = records["device"]
    device["platform"]["slug"] = "arista_eos"
    device["device_type"]["manufacturer"]["slug"] = "arista"
    gateway = ipaddress.ip_interface(records["ip"]["address"]).network.network_address + 1
    device["config_context"]["ztp"] = {
        "management": {"gateway": str(gateway), "interface": "Management1", "vrf": "default"}
    }
    records["ip"]["assigned_object"]["name"] = "Management1"
    intent = netbox[0].get_device_intent("1")
    assert intent.device.platform == "eos"
    assert intent.device.vendor == "arista"
    assert intent.software is None
    device["config_context"]["ztp"]["image"] = {}
    with pytest.raises(ZtpError, match="Arista EOS does not accept software intent"):
        netbox[0].get_device_intent("1")


def test_eos_registration_retries_only_unreachable_controller(monkeypatch):
    module = load_bootstrap()
    calls = []

    def flaky(path, data=None, token=None, limit=None):
        calls.append(path)
        if len(calls) < 3:
            raise module.ControllerUnreachable("Controller unreachable: [Errno 111] refused")
        return b'{"ok": true}'

    monkeypatch.setattr(module, "request", flaky)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    assert module.register({}) == {"ok": True} and len(calls) == 3

    def rejected(path, data=None, token=None, limit=None):
        calls.append(path)
        raise module.BootstrapError("Controller rejected request: HTTP 403")

    calls.clear()
    monkeypatch.setattr(module, "request", rejected)
    with pytest.raises(module.BootstrapError, match="HTTP 403"):
        module.register({})
    assert len(calls) == 1


def test_eos_request_reports_connection_failure_reason(monkeypatch):
    module = load_bootstrap()
    monkeypatch.setattr(module, "CONTROLLER_URL", "http://192.0.2.1")
    monkeypatch.setattr(module, "ALLOW_HTTP", True)

    class Opener:
        def open(self, req, timeout):
            raise module.URLError(ConnectionRefusedError(111, "Connection refused"))

    monkeypatch.setattr(module, "build_opener", lambda *handlers: Opener())
    with pytest.raises(module.ControllerUnreachable, match="Connection refused"):
        module.request("/api/v1/ztp/register", {})
