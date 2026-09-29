import httpx
import pytest

from app.errors import InvalidIntent, InventoryDenied, InventoryUnavailable
from app.inventory.netbox import NetBoxInventoryProvider


def test_normalizes_and_filters_context(netbox):
    provider, calls = netbox
    device = provider.get_device_by_serial("FDO12345678")
    intent = provider.get_device_intent(device.id)
    assert str(intent.management.address) == "192.0.2.10/24"
    assert intent.device.id == "1"
    assert "unrelated_secret" not in intent.model_dump_json()
    assert [c.url.path for c in calls] == [
        "/api/dcim/devices/",
        "/api/dcim/devices/1/",
        "/api/ipam/ip-addresses/10/",
    ]
    assert calls[0].url.params["serial"] == "FDO12345678"
    with pytest.raises(NotImplementedError):
        provider.update_device_status("1", "active")
    assert all(c.method == "GET" for c in calls)


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_enablement_requires_boolean_true(records, netbox, value):
    records["device"]["config_context"]["provisioning"]["ztp_enabled"] = value
    with pytest.raises(InventoryDenied):
        netbox[0].get_device_intent("1")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["device"].pop("config_context"),
        lambda r: r["device"]["config_context"]["ztp"].pop("target_nxos"),
        lambda r: r["device"]["config_context"]["ztp"]["image"].update(sha256="bad"),
        lambda r: r["device"]["config_context"]["ztp"]["image"].update(filename="../evil.bin"),
        lambda r: r["device"]["config_context"]["ztp"]["management"].update(gateway="198.51.100.1"),
        lambda r: r["device"].update(primary_ip4=None),
        lambda r: r["ip"]["assigned_object"]["device"].update(id=2),
        lambda r: r["ip"]["assigned_object"].update(name="Ethernet1/1"),
        lambda r: r["ip"].update(assigned_object_type="virtualization.vminterface"),
        lambda r: r["ip"].update(address="192.0.2.11/24"),
        lambda r: r["device"].update(name="leaf\nreload"),
    ],
)
def test_invalid_intent_fails_closed(records, netbox, mutation):
    mutation(records)
    with pytest.raises(InvalidIntent):
        netbox[0].get_device_intent("1")


@pytest.mark.parametrize("count", [0, 2])
def test_unknown_or_duplicate_serial(records, count):
    provider = NetBoxInventoryProvider(
        "https://netbox.test",
        "token",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json={"results": [records["device"]] * count, "next": None}
            ),
        ),
    )
    with pytest.raises(InventoryDenied):
        provider.get_device_by_serial("FDO12345678")
    provider.close()


def test_follows_pagination_and_rejects_duplicate(records):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "results": [records["device"]],
                "next": "https://netbox.test/api/dcim/devices/?offset=1"
                if len(calls) == 1
                else None,
            },
        )

    provider = NetBoxInventoryProvider(
        "https://netbox.test", "token", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InventoryDenied):
        provider.get_device_by_serial("FDO12345678")
    assert len(calls) == 2
    provider.close()


def test_no_credential_forwarding_to_untrusted_pagination():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": [], "next": "https://evil.test/steal"})

    provider = NetBoxInventoryProvider(
        "https://netbox.test", "token", transport=httpx.MockTransport(handler)
    )
    with pytest.raises(InventoryUnavailable):
        provider.get_device_by_serial("FDO12345678")
    assert len(calls) == 1
    provider.close()


@pytest.mark.parametrize("status", [301, 401, 403, 429, 500, 503])
def test_upstream_errors_are_unavailable(status):
    provider = NetBoxInventoryProvider(
        "https://netbox.test",
        "token",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(status, text="sensitive upstream details"),
        ),
    )
    with pytest.raises(InventoryUnavailable):
        provider.get_device_by_serial("FDO12345678")
    provider.close()


@pytest.mark.parametrize("verify_ssl", [True, False])
def test_factory_applies_netbox_tls_setting(monkeypatch, verify_ssl):
    from app.inventory.factory import build_provider
    from app.settings import Settings

    captured = {}
    original_client = httpx.Client

    def client(**kwargs):
        captured.update(kwargs)
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    settings = Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        netbox_url="https://netbox.test",
        netbox_token="test-token",
        netbox_verify_ssl=verify_ssl,
    )
    provider = build_provider(settings)
    try:
        assert captured["verify"] is verify_ssl
        assert captured["follow_redirects"] is False
    finally:
        provider.close()
