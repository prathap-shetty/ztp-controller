import base64
import hashlib
import re
import struct

from jinja2 import Environment, StrictUndefined

from app.errors import InvalidIntent, ZtpError
from app.models.contracts import ConfigArtifact, DeviceIntent, Manifest
from app.settings import Settings


def render_configuration(intent: DeviceIntent, settings: Settings) -> tuple[str, str]:
    if intent.initial_configuration is None:
        raise InvalidIntent()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,62}", intent.device.name):
        raise InvalidIntent()
    key = settings.ssh_public_key_file.read_text().strip().split()
    if len(key) < 2 or key[0] != "ssh-rsa":
        raise ValueError("M2a requires an OpenSSH RSA public key")
    try:
        wire = base64.b64decode(key[1], validate=True)
        values, offset = [], 0
        for _ in range(3):
            size = struct.unpack_from("!I", wire, offset)[0]
            offset += 4
            if size > len(wire) - offset:
                raise ValueError("Truncated public key")
            values.append(wire[offset : offset + size])
            offset += size
        if (
            offset != len(wire)
            or values[0] != b"ssh-rsa"
            or not 2048 <= int.from_bytes(values[2], "big").bit_length() <= 4096
            or int.from_bytes(values[1], "big") < 3
        ):
            raise ValueError("Expected a 2048-4096 bit RSA public key")
    except (ValueError, struct.error) as exc:
        raise ValueError("Invalid RSA public key") from exc
    template = settings.template_path.read_text()
    rendered = (
        Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
        .from_string(template)
        .render(
            hostname=intent.device.name,
            username=settings.ssh_username,
            public_key=" ".join(key[:2]),
            management=intent.management.model_dump(mode="json"),
            initial=intent.initial_configuration.model_dump(mode="json"),
        )
    )
    if len(rendered.encode()) > 262144:
        raise InvalidIntent()
    return rendered, hashlib.sha256(template.encode()).hexdigest()


def configuration_manifest(
    manifest: Manifest, intent: DeviceIntent, settings: Settings
) -> tuple[Manifest, str | None]:
    if settings.execution_mode == "planning-only":
        return manifest, None
    profile = manifest.compatibility_profile
    upgrading = settings.execution_mode == "upgrade-and-configure"
    if upgrading and (
        not profile.upgrade_path_approved or profile.install_method != "poap-install-no-reload"
    ):
        raise ZtpError("upgrade_not_approved", 403, "Upgrade path requires explicit approval")
    if manifest.upgrade_required and not upgrading:
        raise ZtpError("target_version_required", 409, "M2a requires the running target version")
    if profile.replay_method != "scheduled-config-exit":
        raise ZtpError("replay_not_qualified", 403, "No supported replay method configured")
    if not profile.hardware_qualified and not settings.allow_unqualified_lab:
        raise ZtpError("replay_not_qualified", 403, "Profile requires explicit lab qualification")
    config, template_hash = render_configuration(intent, settings)
    payload = manifest.model_dump(mode="json")
    payload.update(
        mode=settings.execution_mode,
        execution_enabled=True,
        actions=(
            ["download-image", "install-image", "stage-config"]
            if upgrading and manifest.upgrade_required
            else ["stage-config"]
        ),
        config=ConfigArtifact(
            sha256=hashlib.sha256(config.encode()).hexdigest(), size_bytes=len(config.encode())
        ).model_dump(),
        template_revision=template_hash,
        bootstrap_revision=hashlib.sha256(settings.bootstrap_path.read_bytes()).hexdigest(),
    )
    return Manifest.model_validate(payload), config
