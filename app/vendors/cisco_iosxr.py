"""IOS XR configuration-only adapter. No image or installation capabilities."""

import hashlib
import re
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from app.errors import InvalidIntent, ZtpError
from app.models.contracts import ConfigArtifact, XrManifest
from app.services.hashing import stable_hash


class CiscoIosxrAdapter:
    def prepare(self, intent, observed, settings):
        if not settings.xr_enabled or not settings.lite_mode:
            raise ZtpError("xr_disabled", 403, "Enable IOS XR configuration-only support in Lite")
        if settings.execution_mode == "planning-only":
            raise ZtpError("execution_disabled", 403, "Configuration execution is disabled")
        if intent.device.platform != "iosxr" or observed.platform != "iosxr":
            raise InvalidIntent("platform_mismatch", "IOS XR identity required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", intent.device.name):
            raise InvalidIntent()
        template = Path("templates/cisco/iosxr_lite.j2").read_text()
        config = (
            Environment(undefined=StrictUndefined, autoescape=False)
            .from_string(template)
            .render(
                hostname=intent.device.name,
                password=settings.admin_password.get_secret_value(),
                management=intent.management,
            )
        )
        return XrManifest(
            device_id=intent.device.id,
            serial_number=intent.device.serial_number,
            intent_hash=stable_hash(intent.model_dump(mode="json")),
            management=intent.management,
            config=ConfigArtifact(
                sha256=hashlib.sha256(config.encode()).hexdigest(), size_bytes=len(config.encode())
            ),
            template_revision=hashlib.sha256(template.encode()).hexdigest(),
            bootstrap_revision=hashlib.sha256(settings.xr_bootstrap_path.read_bytes()).hexdigest(),
        ), config
