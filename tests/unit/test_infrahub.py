import json

import httpx
import pytest
from pydantic import ValidationError

from app.errors import InvalidIntent, InventoryDenied, InventoryUnavailable
from app.inventory.infrahub import InfraHubInventoryProvider
from app.settings import Settings


def attr(value):
    return {"value": value}


@pytest.fixture
def node():
    return {
        "id": "device-1",
        "name": attr("leaf-1"),
        "serial": attr("SERIAL01"),
        "status": attr("staged"),
        "platform": {"node": {"name": attr("cisco_nxos")}},
        "device_type": {
            "node": {
                "name": attr("N9K-C9300V"),
                "part_number": attr(None),
                "manufacturer": {"node": {"name": attr("Cisco")}},
            }
        },
        "mgmt_interface": attr("mgmt0"),
        "primary_address": {
            "node": {
                "address": attr("192.168.20.50/24"),
                "interface": {
                    "node": {"name": attr("mgmt0"), "device": {"node": {"id": "device-1"}}}
                },
            }
        },
        "ztp_enabled": attr(True),
        "ztp_gateway": attr("192.168.20.1"),
        "ztp_target_version": attr("10.5(4)"),
        "ztp_image_name": attr("nxos.bin"),
        "ztp_image_sha256": attr("a" * 64),
        "ztp_initial_configuration": attr(None),
    }


def provider(node, count=1, has_role=True, queries=None):
    def handle(request):
        assert request.method == "POST"
        assert str(request.url) == "https://infrahub.test/graphql/main"
        assert request.headers["X-INFRAHUB-KEY"] == "test-token"
        payload = json.loads(request.content)
        assert payload["query"].startswith("query")
        assert "mutation" not in payload["query"]
        if queries is not None:
            queries.append(payload["query"])
        if "__type" in payload["query"]:
            fields = [{"name": "name"}, {"name": "serial"}] + ([{"name": "role"}] * has_role)
            return httpx.Response(200, json={"data": {"__type": {"fields": fields}}})
        return httpx.Response(
            200, json={"data": {"DcimDevice": {"edges": [{"node": node}] * count}}}
        )

    return InfraHubInventoryProvider(
        "https://infrahub.test", "test-token", transport=httpx.MockTransport(handle)
    )


def test_identity_and_intent_revocation(node):
    with_provider = provider(node)
    assert with_provider.get_device_by_serial("SERIAL01").vendor == "cisco"
    assert str(with_provider.get_device_intent("device-1").management.address) == "192.168.20.50/24"
    node["ztp_enabled"] = attr(False)
    with pytest.raises(InventoryDenied, match="not enabled"):
        with_provider.get_device_intent("device-1")
    with_provider.close()


@pytest.mark.parametrize("fault", ["platform", "interface", "owner", "checksum", "gateway"])
def test_invalid_intent(node, fault):
    if fault == "platform":
        node["platform"]["node"] = None
    if fault == "interface":
        node["mgmt_interface"] = attr("Ethernet1/1")
    if fault == "owner":
        node["primary_address"]["node"]["interface"]["node"]["device"]["node"]["id"] = "other"
    if fault == "checksum":
        node["ztp_image_sha256"] = attr("a" * 64 + " ")
    if fault == "gateway":
        node["ztp_gateway"] = attr("10.10.10.1")
    with pytest.raises(InvalidIntent):
        provider(node).get_device_intent("device-1")


@pytest.mark.parametrize("count", [0, 2])
def test_duplicate_or_unknown_serial(node, count):
    with pytest.raises(InventoryDenied):
        provider(node, count).get_device_by_serial("SERIAL01")


def test_graphql_errors_do_not_leak():
    p = InfraHubInventoryProvider(
        "https://infrahub.test",
        "secret",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"errors": [{"message": "sensitive error"}]})
        ),
    )
    with pytest.raises(InventoryUnavailable) as e:
        p.check_ready()
    assert "sensitive" not in str(e.value)


def test_infrahub_settings_no_netbox_credentials_needed():
    s = Settings(
        database_url="postgresql://example",
        inventory_provider="infrahub",
        infrahub_url="https://infrahub.test",
        infrahub_token="token",
    )
    assert s.netbox_token is None
    with pytest.raises(ValidationError):
        Settings(
            database_url="postgresql://example",
            inventory_provider="infrahub",
            infrahub_url="http://infrahub.test",
            infrahub_token="token",
        )


def test_xr_config_only_intent(node):
    node["platform"]["node"]["name"] = attr("cisco_iosxr")
    node["mgmt_interface"] = attr("MgmtEth0/RP0/CPU0/0")
    node["primary_address"]["node"]["interface"]["node"]["name"] = attr("MgmtEth0/RP0/CPU0/0")
    for key in ("ztp_target_version", "ztp_image_name", "ztp_image_sha256"):
        node[key] = attr(None)
    p = provider(node)
    intent = p.get_device_intent("device-1")
    assert intent.device.platform == "iosxr"
    assert intent.management.vrf == "default"
    assert intent.software is None
    node["ztp_image_name"] = attr("some-image.iso")
    with pytest.raises(InvalidIntent, match="software intent"):
        p.get_device_intent("device-1")
    p.close()


def test_xe_config_only_intent(node):
    node["platform"]["node"]["name"] = attr("cisco_iosxe")
    node["mgmt_interface"] = attr("GigabitEthernet1")
    node["primary_address"]["node"]["interface"]["node"]["name"] = attr("GigabitEthernet1")
    for key in ("ztp_target_version", "ztp_image_name", "ztp_image_sha256"):
        node[key] = attr(None)
    p = provider(node)
    intent = p.get_device_intent("device-1")
    assert intent.device.platform == "iosxe"
    assert intent.management.vrf == "default"
    assert intent.software is None
    node["ztp_image_name"] = attr("c8000v.bin")
    with pytest.raises(InvalidIntent, match="IOS XE does not accept software intent"):
        p.get_device_intent("device-1")
    p.close()


def test_eos_config_only_intent(node):
    node["platform"]["node"]["name"] = attr("arista_eos")
    node["device_type"]["node"]["manufacturer"]["node"]["name"] = attr("Arista")
    node["mgmt_interface"] = attr("Management1")
    node["primary_address"]["node"]["interface"]["node"]["name"] = attr("Management1")
    for key in ("ztp_target_version", "ztp_image_name", "ztp_image_sha256"):
        node[key] = attr(None)
    p = provider(node)
    intent = p.get_device_intent("device-1")
    assert intent.device.platform == "eos"
    assert intent.device.vendor == "arista"
    assert intent.software is None
    node["ztp_image_name"] = attr("EOS.swi")
    with pytest.raises(InvalidIntent, match="Arista EOS does not accept software intent"):
        p.get_device_intent("device-1")
    p.close()


def test_role_is_read_only_when_the_schema_has_it(node):
    node["role"] = attr("Leaf")
    queries = []
    assert provider(node, queries=queries).get_device_by_serial("SERIAL01").role == "leaf"
    assert sum("__type" in q for q in queries) == 1 and "role { value }" in queries[-1]
    node["role"] = attr("Border Leaf")  # Not a slug: no role template, registration continues.
    assert provider(node).get_device_by_serial("SERIAL01").role is None
    queries.clear()
    node.pop("role")
    without = provider(node, has_role=False, queries=queries)
    assert without.get_device_by_serial("SERIAL01").role is None
    assert "role" not in queries[-1] and "ROLE_FIELD" not in queries[-1]


def test_invalid_fields_are_named_without_values(node):
    node["device_type"]["node"]["part_number"] = attr("Arista vEOS-lab")
    with pytest.raises(InvalidIntent) as identity_error:
        provider(node).get_device_by_serial("SERIAL01")
    assert identity_error.value.message.endswith(": model")
    assert "vEOS" not in identity_error.value.message


def test_nxos_without_software_fields_is_configuration_only(node):
    for key in ("ztp_target_version", "ztp_image_name", "ztp_image_sha256"):
        node[key] = attr(None)
    assert provider(node).get_device_intent("device-1").software is None


def test_partial_nxos_software_fields_are_rejected(node):
    node["ztp_image_name"] = attr(None)
    with pytest.raises(InvalidIntent) as error:
        provider(node).get_device_intent("device-1")
    assert error.value.code == "nxos_software_incomplete"
