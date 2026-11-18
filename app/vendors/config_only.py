"""Shared configuration-only adapter. No image or installation capabilities."""

import hashlib
import re
from pathlib import Path

from jinja2 import Environment, StrictUndefined

from app.errors import InvalidIntent, ZtpError
from app.models.contracts import ConfigArtifact
from app.services.hashing import stable_hash


class ConfigOnlyAdapter:
    platform: str
    label: str
    template: Path
    manifest_type: type
    enabled_setting: str
    bootstrap_setting: str
    disabled_code: str

    @classmethod
    def enabled(cls, settings) -> bool:
        return bool(getattr(settings, cls.enabled_setting)) and settings.lite_mode

    def prepare(self, intent, observed, settings):
        if not self.enabled(settings):
            raise ZtpError(
                self.disabled_code, 403, f"Enable {self.label} configuration-only support in Lite"
            )
        if settings.execution_mode == "planning-only":
            raise ZtpError("execution_disabled", 403, "Configuration execution is disabled")
        if intent.device.platform != self.platform or observed.platform != self.platform:
            raise InvalidIntent("platform_mismatch", f"{self.label} identity required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", intent.device.name):
            raise InvalidIntent()
        template = self.template.read_text()
        config = (
            Environment(undefined=StrictUndefined, autoescape=False)
            .from_string(template)
            .render(
                hostname=intent.device.name,
                password=settings.admin_password.get_secret_value(),
                management=intent.management,
            )
        )
        bootstrap = getattr(settings, self.bootstrap_setting)
        return self.manifest_type(
            device_id=intent.device.id,
            serial_number=intent.device.serial_number,
            intent_hash=stable_hash(intent.model_dump(mode="json")),
            management=intent.management,
            config=ConfigArtifact(
                sha256=hashlib.sha256(config.encode()).hexdigest(), size_bytes=len(config.encode())
            ),
            template_revision=hashlib.sha256(template.encode()).hexdigest(),
            bootstrap_revision=hashlib.sha256(bootstrap.read_bytes()).hexdigest(),
        ), config
