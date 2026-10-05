from app.inventory.base import InventoryProvider
from app.inventory.infrahub import InfraHubInventoryProvider
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.inventory.netbox import NetBoxInventoryProvider
from app.settings import Settings


def build_provider(settings: Settings) -> InventoryProvider:
    if settings.inventory_provider == "infrahub":
        token = (
            settings.infrahub_token_file.read_text().strip()
            if settings.infrahub_token_file
            else settings.infrahub_token.get_secret_value()
        )
        if not token or any(c.isspace() for c in token):
            raise ValueError("Invalid InfraHub token")
        return InfraHubInventoryProvider(
            settings.infrahub_url,
            token,
            settings.infrahub_branch,
            settings.infrahub_platform,
            settings.infrahub_verify_ssl,
        )
    if settings.inventory_provider == "local-yaml":
        return LocalYamlInventoryProvider(settings.local_inventory_path)
    return NetBoxInventoryProvider(
        settings.netbox_url,
        settings.token(),
        settings.netbox_platform_slug,
        settings.netbox_auth_scheme,
        verify_ssl=settings.netbox_verify_ssl,
        branch=settings.netbox_branch,
    )
