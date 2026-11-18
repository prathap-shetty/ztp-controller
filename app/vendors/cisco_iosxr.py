"""IOS XR configuration-only adapter. No image or installation capabilities."""

from pathlib import Path

from app.models.contracts import XrManifest
from app.vendors.config_only import ConfigOnlyAdapter


class CiscoIosxrAdapter(ConfigOnlyAdapter):
    platform = "iosxr"
    label = "IOS XR"
    template = Path("templates/cisco/iosxr_lite.j2")
    manifest_type = XrManifest
    enabled_setting = "xr_enabled"
    bootstrap_setting = "xr_bootstrap_path"
    disabled_code = "xr_disabled"
