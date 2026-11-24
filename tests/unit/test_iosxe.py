import hashlib
import importlib.util
import ipaddress
import json
from collections import namedtuple
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.errors import ZtpError
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.models.contracts import DeviceIntent, ObservedDevice, parse_manifest
from app.settings import Settings
from app.vendors.cisco_iosxe import CiscoIosxeAdapter
from app.vendors.cisco_iosxr import CiscoIosxrAdapter

ConfigResult = namedtuple("ConfigResult", "success command line output notes")


def xe_intent():
    data = LocalYamlInventoryProvider(Path("inventory/devices.iosxe.example.yaml")).list_entries()[
        0
    ]
    payload = data.model_dump()
    payload["ztp_enabled"] = True
    return DeviceIntent.model_validate(payload)


def xe_observed():
    return ObservedDevice(
        serial_number="REPLACE_WITH_CHASSIS_SERIAL",
        model="C8000V",
        vendor="cisco",
        platform="iosxe",
        current_version="17.09.04a",
        management_mac="52:54:00:44:55:66",
    )


def xe_settings():
    return Settings(
        database_url="postgresql://unused",
        inventory_provider="local-yaml",
        lite_mode=True,
        xe_enabled=True,
        execution_mode="configuration-only",
        admin_password="TestPassword123",
    )


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("xe_bootstrap", "poap/cisco/iosxe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_xe_manifest_and_render():
    manifest, config = CiscoIosxeAdapter().prepare(xe_intent(), xe_observed(), xe_settings())
    assert manifest.platform == "iosxe"
    assert manifest.actions == ("apply-config",)
    assert not manifest.upgrade_required
    assert manifest.validation_policy == "manual"
    assert "target" not in manifest.model_dump()
    assert parse_manifest(manifest.model_dump(mode="json")) == manifest
    assert "interface GigabitEthernet1" in config
    assert "ip address 10.10.10.110 255.255.255.0" in config
    assert "ip route 0.0.0.0 0.0.0.0 10.10.10.1" in config
    assert "secret 0 TestPassword123" in config
    assert "mgmt0" not in config and "MgmtEth" not in config
    with pytest.raises(ZtpError, match="Enable IOS XE"):
        CiscoIosxeAdapter().prepare(
            xe_intent(), xe_observed(), xe_settings().model_copy(update={"xe_enabled": False})
        )
    # XR enablement must not enable XE, and the XR adapter rejects XE identities.
    with pytest.raises(ZtpError, match="Enable IOS XE"):
        CiscoIosxeAdapter().prepare(
            xe_intent(),
            xe_observed(),
            xe_settings().model_copy(update={"xe_enabled": False, "xr_enabled": True}),
        )
    with pytest.raises(ZtpError, match="IOS XR identity"):
        CiscoIosxrAdapter().prepare(
            xe_intent(), xe_observed(), xe_settings().model_copy(update={"xr_enabled": True})
        )


def test_xe_rejects_software_and_foreign_management():
    payload = xe_intent().model_dump()
    payload["software"] = {
        "target_version": "17.09.04a",
        "image_name": "c8000v.bin",
        "image_checksum": "a" * 64,
    }
    with pytest.raises(ValidationError):
        DeviceIntent.model_validate(payload)
    payload["software"] = None
    for interface, vrf in [("mgmt0", "management"), ("MgmtEth0/RP0/CPU0/0", "default")]:
        payload["management"].update(interface=interface, vrf=vrf)
        with pytest.raises(ValidationError):
            DeviceIntent.model_validate(payload)


def outputs():
    return {
        "show inventory": (
            'NAME: "Chassis", DESCR: "Cisco C8000V Chassis"\n'
            "PID: C8000V            , VID: V00  , SN: 9abcdefghij\n\n"
            'NAME: "module R0", DESCR: "Cisco C8000V Route Processor"\n'
            "PID: C8000V            , VID: V00  , SN: OTHER"
        ),
        "show version": "Cisco IOS XE Software, Version 17.09.04a\nCisco IOS Software [Cupertino]",
        "show ip interface brief": (
            "Interface              IP-Address      OK? Method Status                Protocol\n"
            "GigabitEthernet1       10.10.10.42     YES DHCP   up                    up\n"
            "GigabitEthernet2       unassigned      YES unset  administratively down down"
        ),
        "show interfaces GigabitEthernet1": (
            "GigabitEthernet1 is up, line protocol is up\n"
            "  Hardware is vNIC, address is 5254.0044.5566 (bia 5254.0044.5566)"
        ),
    }


def test_xe_discovery():
    module = load_bootstrap()
    data = outputs()

    class Cli:
        @staticmethod
        def execute(text):
            return data[text]

    observed, interface = module.discover(Cli)
    assert observed == {
        "serial_number": "9ABCDEFGHIJ",
        "model": "C8000V",
        "vendor": "cisco",
        "platform": "iosxe",
        "current_version": "17.09.04a",
        "management_mac": "52:54:00:44:55:66",
    }
    assert interface == "GigabitEthernet1"
    ObservedDevice.model_validate(observed)
    data["show ip interface brief"] += "\nGigabitEthernet2       10.20.0.5       YES DHCP   up up"
    with pytest.raises(module.BootstrapError, match="DHCP management interface"):
        module.discover(Cli)
    data.update(outputs())
    data["show inventory"] = 'NAME: "module R0", DESCR: "RP"\nPID: C8000V, VID: V00, SN: OTHER'
    with pytest.raises(module.BootstrapError, match="chassis"):
        module.discover(Cli)


@pytest.mark.parametrize(
    "scenario", ["success", "checksum", "apply-failed", "save-failed", "callback-failed"]
)
def test_xe_bootstrap_apply(monkeypatch, tmp_path, scenario):
    module = load_bootstrap()
    manifest, config = CiscoIosxeAdapter().prepare(xe_intent(), xe_observed(), xe_settings())
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

    class Cli:
        @staticmethod
        def configure(lines):
            applied.append(lines)
            ok = scenario != "apply-failed"
            return [ConfigResult(ok or n != 1, line, n, "", "") for n, line in enumerate(lines)]

        @staticmethod
        def execute(text):
            assert text == "write memory"
            return "Building configuration...\n" + ("" if scenario == "save-failed" else "[OK]")

    (tmp_path / "flash").mkdir()
    monkeypatch.setattr(module, "request", request)
    monkeypatch.setattr(
        module, "CHECKPOINT_ROOTS", (str(tmp_path / "missing"), str(tmp_path / "flash"))
    )
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    observed = xe_observed().model_dump()
    if scenario in {"checksum", "apply-failed", "save-failed"}:
        with pytest.raises(module.BootstrapError):
            module.apply(Cli, observed, "GigabitEthernet1")
        assert len(applied) == (0 if scenario == "checksum" else 1)
        if scenario != "checksum":
            assert events == ["CONFIG_APPLYING", "FAILED"]
            # The pending marker blocks a blind reapply after a partial apply.
            with pytest.raises(module.BootstrapError, match="uncertain"):
                module.apply(Cli, observed, "GigabitEthernet1")
            assert len(applied) == 1
        return
    assert module.apply(Cli, observed, "GigabitEthernet1") == 0
    lines = applied[0]
    assert "hostname lab-c8000v-01" in lines
    assert not any(line.startswith("!") or line == "end" for line in lines)
    assert hashlib.sha256(config.encode()).hexdigest() == manifest.config.sha256
    # A committed checkpoint suppresses reapplication even if the callback failed.
    assert module.apply(Cli, observed, "GigabitEthernet1") == 0
    assert len(applied) == 1
    with pytest.raises(module.BootstrapError, match="unsupported XE plan"):
        module.apply(Cli, observed, "GigabitEthernet2")


def test_xe_bootstrap_requires_flash(monkeypatch, tmp_path):
    module = load_bootstrap()
    monkeypatch.setattr(module, "CHECKPOINT_ROOTS", (str(tmp_path / "missing"),))
    with pytest.raises(module.BootstrapError, match="flash"):
        module.checkpoint_directory()


def test_netbox_xe_without_image(records, netbox):
    device = records["device"]
    device["platform"]["slug"] = "cisco_iosxe"
    gateway = ipaddress.ip_interface(records["ip"]["address"]).network.network_address + 1
    device["config_context"]["ztp"] = {
        "management": {"gateway": str(gateway), "interface": "GigabitEthernet1", "vrf": "default"}
    }
    records["ip"]["assigned_object"]["name"] = "GigabitEthernet1"
    intent = netbox[0].get_device_intent("1")
    assert intent.device.platform == "iosxe"
    assert intent.software is None
    device["config_context"]["ztp"]["image"] = {}
    with pytest.raises(ZtpError, match="IOS XE does not accept software intent"):
        netbox[0].get_device_intent("1")


def test_xe_registration_retries_only_unreachable_controller(monkeypatch):
    module = load_bootstrap()
    calls = []

    def flaky(path, data=None, token=None, limit=None):
        calls.append(path)
        if len(calls) < 4:
            raise module.ControllerUnreachable("Controller unreachable: [Errno 101] unreachable")
        return b'{"ok": true}'

    monkeypatch.setattr(module, "request", flaky)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    assert module.register({}) == {"ok": True} and len(calls) == 4

    def rejected(path, data=None, token=None, limit=None):
        calls.append(path)
        raise module.BootstrapError("Controller rejected request: HTTP 403")

    calls.clear()
    monkeypatch.setattr(module, "request", rejected)
    with pytest.raises(module.BootstrapError, match="HTTP 403"):
        module.register({})
    assert len(calls) == 1


def test_xe_request_reports_connection_failure_reason(monkeypatch):
    module = load_bootstrap()
    monkeypatch.setattr(module, "CONTROLLER_URL", "http://192.0.2.1")
    monkeypatch.setattr(module, "ALLOW_HTTP", True)

    class Opener:
        def open(self, req, timeout):
            raise module.URLError(OSError(101, "Network is unreachable"))

    monkeypatch.setattr(module, "build_opener", lambda *handlers: Opener())
    with pytest.raises(module.ControllerUnreachable, match="Network is unreachable"):
        module.request("/api/v1/ztp/register", {})


def test_xe_checkpoint_skips_unwritable_flash_and_reports_each_mount(monkeypatch, tmp_path):
    module = load_bootstrap()
    locked, writable = tmp_path / "flash", tmp_path / "bootflash"
    locked.mkdir()
    writable.mkdir()
    real_makedirs = module.os.makedirs

    def makedirs(path, mode=0o777, exist_ok=False):
        if path.startswith(str(locked)):
            raise PermissionError(13, "Permission denied", path)
        return real_makedirs(path, mode=mode, exist_ok=exist_ok)

    monkeypatch.setattr(module.os, "makedirs", makedirs)
    monkeypatch.setattr(module, "CHECKPOINT_ROOTS", (str(locked), str(writable)))
    assert module.checkpoint_directory() == str(writable / "ztp-lite")
    monkeypatch.setattr(module, "CHECKPOINT_ROOTS", (str(locked),))
    with pytest.raises(module.BootstrapError, match="flash: Permission denied"):
        module.checkpoint_directory()
