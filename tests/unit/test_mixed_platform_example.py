import json
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_mixed_platform_classes_select_each_bootstrap_without_a_default():
    dhcp = json.loads((ROOT / "examples/mixed-platform/kea.json").read_text())["Dhcp4"]
    classes = {c["name"]: c for c in dhcp["client-classes"]}
    boot = {name: {o["name"]: o["data"] for o in c["option-data"]} for name, c in classes.items()}

    assert boot["ztp-eos"]["boot-file-name"] == "http://10.10.10.1/bootstrap/eos.py"
    assert boot["ztp-iosxe"]["boot-file-name"] == "http://10.10.10.1/bootstrap/iosxe.py"
    assert boot["ztp-iosxr"]["boot-file-name"] == "http://10.10.10.1/bootstrap/iosxr.py"
    # XR ZTP sends user class "exr-config" (or "xr-config" on XR 25.x) as plain
    # text; its iPXE stage sends "iPXE".
    assert classes["ztp-iosxr"]["test"] == (
        "option[77].hex == 'exr-config' or option[77].hex == 'xr-config'"
    )
    # Every XR sends the literal client ID "Not Specified": lease by MAC instead.
    assert dhcp["subnet4"][0]["match-client-id"] is False
    assert boot["ztp-nxos"] == {
        "tftp-server-name": "http://10.10.10.1",
        "boot-file-name": "bootstrap/nxos.py",
    }
    # Option 77 is RFC 3004: a length byte (0x0a) precedes "Cisco-POAP".
    assert classes["ztp-nxos"]["test"].endswith("== 0x0a" + b"Cisco-POAP".hex())
    assert "tftp-server-name" not in boot["ztp-eos"] | boot["ztp-iosxe"] | boot["ztp-iosxr"]
    subnet_options = {o["name"] for o in dhcp["subnet4"][0]["option-data"]}
    assert subnet_options == {"routers", "domain-name-servers"}
