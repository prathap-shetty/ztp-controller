"""Lite NX-OS plans derived from NetBox or InfraHub intent rather than a duplicate catalog."""

import os
import stat

from app.errors import InventoryDenied, ZtpError
from app.models.contracts import CompatibilityProfile
from app.vendors.cisco_nxos import CiscoNxosAdapter
from app.vendors.nxos_version import version_satisfies


class NetBoxNxosAdapter(CiscoNxosAdapter):
    def __init__(
        self,
        image_directory,
        skip_source_validation=False,
        allow_newer_version=False,
        source="netbox",
    ):
        # The source names the profile and messages; NetBox keeps its original
        # values so existing attempt plan hashes are unchanged.
        self.source, self.label = source, {"netbox": "NetBox", "infrahub": "InfraHub"}[source]
        self.image_directory = image_directory
        self.skip_source_validation = skip_source_validation
        self.allow_newer_version = allow_newer_version

    def build_manifest(self, intent, observed):
        software = intent.software
        try:
            satisfied = version_satisfies(
                observed.current_version, software.target_version, self.allow_newer_version
            )
        except ValueError:
            raise InventoryDenied() from None
        # Inventory intent has no approved source-release list. Never manufacture
        # approval for a source release when validation is enabled.
        if not satisfied and not self.skip_source_validation:
            raise InventoryDenied(
                "source_validation_required",
                self.label
                + " upgrade requires Lite source bypass or an explicit catalog source policy",
            )
        descriptor = None
        try:
            descriptor = os.open(
                self.image_directory / software.image_name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            )
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
                raise ValueError()
            size = info.st_size
        except (OSError, ValueError):
            raise ZtpError(
                "image_unavailable",
                409,
                self.label + " image must exist as a non-empty regular file in the image directory",
            ) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        profile = CompatibilityProfile(
            id=self.source + "-" + intent.device.id,
            model=intent.device.model,
            source_versions=[software.target_version],
            target_version=software.target_version,
            image_name=software.image_name,
            image_checksum=software.image_checksum,
            image_size_bytes=size,
            hardware_qualified=False,
            upgrade_path_approved=True,
            install_method="poap-install-no-reload",
            replay_method="scheduled-config-exit",
        )
        return self._build_manifest(intent, observed, [profile])
