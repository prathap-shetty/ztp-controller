import json
import uuid

import pytest
from pydantic import ValidationError

from app.models.contracts import DeviceIntent, Manifest, ObservedDevice
from app.vendors.netbox_nxos import NetBoxNxosAdapter
from tests.unit.test_m2a import load_bootstrap


def test_nxos_intent_without_software_is_valid_but_still_needs_mgmt0(netbox, records):
    ztp = records["device"]["config_context"]["ztp"]
    ztp.pop("target_nxos")
    ztp.pop("image")
    intent = netbox[0].get_device_intent("1")
    assert intent.software is None and intent.management.interface == "mgmt0"
    data = intent.model_dump(mode="json")
    data["management"]["interface"] = "Ethernet1/1"
    with pytest.raises(ValidationError):
        DeviceIntent.model_validate(data)


@pytest.mark.parametrize("missing", ["target_nxos", "image"])
def test_partial_nxos_software_intent_is_rejected(netbox, records, missing):
    records["device"]["config_context"]["ztp"].pop(missing)
    with pytest.raises(Exception) as error:
        netbox[0].get_device_intent("1")
    assert getattr(error.value, "code", "") == "nxos_software_incomplete"


def test_unversioned_manifest_cannot_upgrade(netbox, records, tmp_path):
    ztp = records["device"]["config_context"]["ztp"]
    ztp.pop("target_nxos")
    ztp.pop("image")
    intent = netbox[0].get_device_intent("1")
    plan = NetBoxNxosAdapter(tmp_path).build_manifest(intent, ObservedDevice(**records["observed"]))
    assert plan.target is None and plan.compatibility_profile is None and not plan.upgrade_required
    payload = plan.model_dump(mode="json")
    with pytest.raises(ValidationError):
        Manifest.model_validate({**payload, "upgrade_required": True})
    with pytest.raises(ValidationError):
        # A target without its compatibility profile is incoherent.
        Manifest.model_validate(
            {
                **payload,
                "target": {
                    "target_version": "10.5(4)",
                    "image_name": "nxos64-cs.10.5.4.M.bin",
                    "image_checksum": "a" * 64,
                },
            }
        )


def test_bootstrap_configures_any_release_without_software_intent(tmp_path):
    module = load_bootstrap()
    attempt, calls = str(uuid.uuid4()), []
    body = "hostname leaf-1\n"
    manifest = {
        "schema_version": 1,
        "mode": "configuration-only",
        "execution_enabled": True,
        "actions": ["stage-config"],
        "upgrade_required": False,
        "serial_number": "SERIAL01",
        "target": None,
        "compatibility_profile": None,
        "config": {
            "sha256": __import__("hashlib").sha256(body.encode()).hexdigest(),
            "size_bytes": len(body),
        },
    }

    def cli(command):
        calls.append(command)
        if command == "show version | json":
            return json.dumps({"nxos_ver_str": "9.3(8)"})
        if command == "show inventory | json":
            return json.dumps(
                {
                    "TABLE_inv": {
                        "ROW_inv": {
                            "name": "Chassis",
                            "serialnum": "SERIAL01",
                            "productid": "N9K-C9300V",
                        }
                    }
                }
            )
        assert command == "copy bootflash:ztp-" + attempt + ".cfg scheduled-config"
        return "Copy complete."

    def api(path, data=None, token=None):
        if path.endswith("register"):
            return json.dumps(
                {
                    "manifest": manifest,
                    "provisioning_id": attempt,
                    "status_token": "t",
                    "state": "AUTHORIZED",
                }
            ).encode()
        if "/config/" in path:
            return body.encode()
        return b"{}"

    environ = {"POAP_VRF": "management", "POAP_MAC": "001122334455"}
    assert module.run(cli, environ, str(tmp_path), api) == 0
    assert sum(c.startswith("copy ") for c in calls) == 1
    assert not any("install" in c or "reload" in c for c in calls)
    # The bootstrap refuses an unversioned plan that asks to upgrade.
    manifest.update(mode="upgrade-and-configure")
    with pytest.raises(RuntimeError, match="eligible"):
        module.run(cli, environ, str(tmp_path / "other"), api)
