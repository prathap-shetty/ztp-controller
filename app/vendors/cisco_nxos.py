import json
from pathlib import Path

from pydantic import TypeAdapter

from app.errors import InventoryDenied
from app.models.contracts import CompatibilityProfile, DeviceIntent, Manifest, ObservedDevice
from app.services.hashing import stable_hash
from app.vendors.base import ZtpVendorAdapter
from app.vendors.nxos_version import same_nxos_release, version_satisfies


class CiscoNxosAdapter(ZtpVendorAdapter):
    def __init__(
        self,
        catalog_path: Path,
        skip_source_validation: bool = False,
        allow_newer_version: bool = False,
    ):
        self.allow_newer_version = allow_newer_version
        self.skip_source_validation = skip_source_validation
        self.profiles = TypeAdapter(list[CompatibilityProfile]).validate_python(
            json.loads(catalog_path.read_text())
        )
        if len({p.id for p in self.profiles}) != len(self.profiles):
            raise ValueError("Duplicate compatibility profile IDs")

    def bootstrap_type(self) -> str:
        return "nxos-poap"

    def build_manifest(self, intent: DeviceIntent, observed: ObservedDevice) -> Manifest:
        try:
            target_satisfied = version_satisfies(
                observed.current_version, intent.software.target_version, self.allow_newer_version
            )
        except ValueError:
            raise InventoryDenied() from None
        matches = [
            p
            for p in self.profiles
            if (
                p.model == observed.model
                and (
                    self.skip_source_validation
                    or any(
                        same_nxos_release(observed.current_version, v) for v in p.source_versions
                    )
                    or (p.install_method is not None and target_satisfied)
                )
                and p.target_version == intent.software.target_version
                and p.image_name == intent.software.image_name
                and p.image_checksum == intent.software.image_checksum
            )
        ]
        if len(matches) != 1:
            raise InventoryDenied(
                "catalog_mismatch",
                "No unique catalog profile matches model, release and image metadata",
            )
        return Manifest(
            allow_newer_version=self.allow_newer_version,
            source_validation_enabled=not self.skip_source_validation,
            device_id=intent.device.id,
            serial_number=intent.device.serial_number,
            intent_hash=stable_hash(intent.model_dump(mode="json")),
            compatibility_profile=matches[0],
            target=intent.software,
            management=intent.management,
            upgrade_required=not target_satisfied,
        )
