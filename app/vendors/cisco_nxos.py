import json
from pathlib import Path

from pydantic import TypeAdapter

from app.errors import InventoryDenied
from app.models.contracts import CompatibilityProfile, DeviceIntent, Manifest, ObservedDevice
from app.services.hashing import stable_hash
from app.vendors.base import ZtpVendorAdapter


class CiscoNxosAdapter(ZtpVendorAdapter):
    def __init__(self, catalog_path: Path):
        self.profiles = TypeAdapter(list[CompatibilityProfile]).validate_python(
            json.loads(catalog_path.read_text())
        )
        if len({p.id for p in self.profiles}) != len(self.profiles):
            raise ValueError("Duplicate compatibility profile IDs")

    def bootstrap_type(self) -> str:
        return "nxos-poap"

    def build_manifest(self, intent: DeviceIntent, observed: ObservedDevice) -> Manifest:
        matches = [
            p
            for p in self.profiles
            if (
                p.model == observed.model
                and (
                    observed.current_version in p.source_versions
                    or (
                        p.install_method is not None
                        and observed.current_version == p.target_version
                    )
                )
                and p.target_version == intent.software.target_version
                and p.image_name == intent.software.image_name
                and p.image_checksum == intent.software.image_checksum
            )
        ]
        if len(matches) != 1:
            raise InventoryDenied()
        return Manifest(
            device_id=intent.device.id,
            serial_number=intent.device.serial_number,
            intent_hash=stable_hash(intent.model_dump(mode="json")),
            compatibility_profile=matches[0],
            target=intent.software,
            management=intent.management,
            upgrade_required=observed.current_version != intent.software.target_version,
        )
