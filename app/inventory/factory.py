from app.inventory.base import InventoryProvider
from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.inventory.netbox import NetBoxInventoryProvider
from app.settings import Settings


def build_provider(settings: Settings) -> InventoryProvider:
    if settings.inventory_provider == "local-yaml":
        return LocalYamlInventoryProvider(settings.local_inventory_path)
    return NetBoxInventoryProvider(
        settings.netbox_url,
        settings.token(),
        settings.netbox_platform_slug,
        settings.netbox_auth_scheme,
        verify_ssl=settings.netbox_verify_ssl,
    )
