import httpx
import pytest

from app.inventory.infrahub import InfraHubInventoryProvider
from app.inventory.netbox import NetBoxInventoryProvider
from app.settings import Settings


@pytest.mark.parametrize("branch", ["main", "", "td5smq0f"])
def test_netbox_branch_on_every_request(branch):
    seen = []

    def respond(request):
        seen.append(request.headers.get("X-NetBox-Branch"))
        return httpx.Response(200, json={})

    provider = NetBoxInventoryProvider(
        "https://netbox.test", "token", branch=branch, transport=httpx.MockTransport(respond)
    )
    try:
        provider._get("dcim/devices/")
        provider._get("https://netbox.test/api/dcim/devices/?offset=100")
        assert seen == ([None, None] if branch in {"", "main"} else [branch, branch])
    finally:
        provider.client.close()


@pytest.mark.parametrize("branch", ["", "  ", "main", "td5smq0f"])
def test_branch_settings_defaults(branch):
    settings = Settings(
        database_url="postgresql://unused",
        inventory_provider="local-yaml",
        netbox_branch=branch,
        infrahub_branch=branch,
    )
    assert settings.netbox_branch == (branch.strip() or "main")
    assert settings.infrahub_branch == (branch.strip() or "main")


def test_default_branches():
    settings = Settings(database_url="postgresql://unused", inventory_provider="local-yaml")
    assert settings.netbox_branch == settings.infrahub_branch == "main"
    provider = InfraHubInventoryProvider("https://infrahub.test", "token")
    assert provider.endpoint == "https://infrahub.test/graphql/main"
    provider.client.close()


def test_invalid_netbox_branch():
    with pytest.raises(ValueError, match="schema ID"):
        Settings(
            database_url="postgresql://unused",
            inventory_provider="local-yaml",
            netbox_branch="branch-name",
        )
