import json
from ipaddress import ip_address, ip_network
from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_multi_vlan_kea_example_keeps_each_vlan_on_its_own_controller_address():
    dhcp = json.loads((ROOT / "examples/multi-vlan/kea.json").read_text())["Dhcp4"]
    subnets = dhcp["subnet4"]

    assert len({subnet["id"] for subnet in subnets}) == len(subnets) == 2
    assert {subnet["interface"] for subnet in subnets} == set(
        dhcp["interfaces-config"]["interfaces"]
    )
    boot_files = set()
    for subnet in subnets:
        network = ip_network(subnet["subnet"])
        options = {option["name"]: option["data"] for option in subnet["option-data"]}
        first, last = subnet["pools"][0]["pool"].split(" - ")
        controller = ip_address(options["tftp-server-name"].removeprefix("http://"))

        assert ip_address(first) in network and ip_address(last) in network
        assert controller in network
        assert not ip_address(first) <= controller <= ip_address(last)
        boot_files.add(options["boot-file-name"])
    assert len(boot_files) == 2


def test_multi_vlan_override_requires_an_explicit_second_address():
    override = (ROOT / "compose.lite.multi-vlan.yaml").read_text()

    assert "${ZTP_BIND_ADDRESS_2:?" in override
    assert "0.0.0.0" not in override


def test_nxos_kea_example_matches_the_default_lite_scope():
    example = json.loads((ROOT / "examples/nxos/kea.json").read_text())
    assert example == json.loads((ROOT / "deploy/lite/kea.json").read_text())
    options = {o["name"]: o["data"] for o in example["Dhcp4"]["subnet4"][0]["option-data"]}
    assert options["boot-file-name"] == "bootstrap/nxos.py"


def test_kea_configs_always_offer_full_one_hour_leases():
    # NX-OS POAP rejects a lease under one hour. Kea's lease cache would re-offer
    # an existing lease with only its remaining time, so caching is disabled.
    from scripts.kea_profiles import configuration

    configs = [ROOT / "deploy/lite/kea.json", *sorted((ROOT / "examples").glob("*/kea.json"))]
    dhcp = [json.loads(path.read_text())["Dhcp4"] for path in configs]
    dhcp += [configuration(mode)["Dhcp4"] for mode in ("legacy-http", "secure-https")]
    for item in dhcp:
        assert item["cache-threshold"] == 0
        assert item["valid-lifetime"] >= 3600
