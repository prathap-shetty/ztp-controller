"""IOS XE configuration-only adapter. No image or installation capabilities."""

from pathlib import Path

from app.models.contracts import XeManifest
from app.vendors.config_only import ConfigOnlyAdapter


class CiscoIosxeAdapter(ConfigOnlyAdapter):
    platform = "iosxe"
    label = "IOS XE"
    template = Path("templates/cisco/iosxe_lite.j2")
    manifest_type = XeManifest
    enabled_setting = "xe_enabled"
    bootstrap_setting = "xe_bootstrap_path"
    disabled_code = "xe_disabled"
