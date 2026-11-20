"""Arista EOS configuration-only adapter. No image or installation capabilities."""

from pathlib import Path

from app.models.contracts import EosManifest
from app.vendors.config_only import ConfigOnlyAdapter


class AristaEosAdapter(ConfigOnlyAdapter):
    platform = "eos"
    label = "Arista EOS"
    template = Path("templates/arista/eos_lite.j2")
    manifest_type = EosManifest
    enabled_setting = "eos_enabled"
    bootstrap_setting = "eos_bootstrap_path"
    disabled_code = "eos_disabled"
