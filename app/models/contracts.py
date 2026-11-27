from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

Token = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.():-]+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
# Platforms that receive schema-v2 apply-config plans; never NX-OS install or version code.
# Each maps to its error-code prefix and operator label.
CONFIG_ONLY_LABELS = {
    "iosxr": ("xr", "IOS XR"),
    "iosxe": ("xe", "IOS XE"),
    "eos": ("eos", "Arista EOS"),
}
CONFIG_ONLY_PLATFORMS = frozenset(CONFIG_ONLY_LABELS)


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservedDevice(Contract):
    serial_number: Token
    vendor: Literal["cisco", "arista"]
    platform: Literal["nxos", "iosxr", "iosxe", "eos"]
    model: Token
    current_version: Token
    management_mac: Annotated[str, Field(pattern=r"^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")]

    @field_validator("serial_number", "model")
    @classmethod
    def uppercase(cls, value: str) -> str:
        return value.upper()

    @field_validator("management_mac")
    @classmethod
    def lowercase(cls, value: str) -> str:
        return value.lower()

    @model_validator(mode="after")
    def vendor_platform(self):
        if (self.platform == "eos") != (self.vendor == "arista"):
            raise ValueError("Platform does not match vendor")
        return self


class DeviceIdentity(Contract):
    id: Token
    name: Token
    serial_number: Token
    vendor: Token
    platform: Token
    model: Token
    status: Token
    # Inventory device role slug; selects an optional role template include.
    role: Token | None = None

    @model_serializer(mode="wrap")
    def omit_empty_role(self, handler):
        # Devices without a role keep the serialized form, and so the intent and
        # plan hashes, they had before role support.
        data = handler(self)
        if data.get("role") is None:
            data.pop("role", None)
        return data


class ManagementAddress(Contract):
    address: IPv4Interface
    gateway: IPv4Address
    vrf: Literal["management"]
    interface: Literal["mgmt0"]

    @model_validator(mode="after")
    def valid_gateway(self):
        if self.gateway not in self.address.network or self.gateway == self.address.ip:
            raise ValueError("Gateway must be a different address in the management subnet")
        if self.address.network.prefixlen < 31:
            reserved = {
                self.address.network.network_address,
                self.address.network.broadcast_address,
            }
            if self.gateway in reserved or self.address.ip in reserved:
                raise ValueError("Management endpoint cannot be a network or broadcast address")
        return self


class XrManagementAddress(ManagementAddress):
    vrf: Literal["default"] = "default"
    interface: Annotated[
        str, Field(pattern=r"^MgmtEth[0-9]+/(?:RP[0-9]+|[0-9]+)/CPU[0-9]+/[0-9]+$")
    ]


class XeManagementAddress(ManagementAddress):
    vrf: Literal["default"] = "default"
    interface: Annotated[
        str, Field(pattern=r"^(?:GigabitEthernet|TenGigabitEthernet)[0-9]+(?:/[0-9]+){0,2}$")
    ]


class EosManagementAddress(ManagementAddress):
    vrf: Literal["default"] = "default"
    interface: Annotated[str, Field(pattern=r"^Management[0-9]+(?:/[0-9]+)?$")]


class SoftwareIntent(Contract):
    target_version: Token
    image_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.bin$", max_length=200)]
    image_checksum: Digest


class InitialConfiguration(Contract):
    dns_servers: list[IPv4Address] = Field(default_factory=list, max_length=3)
    ntp_servers: list[IPv4Address] = Field(default_factory=list, max_length=4)
    ssh_sources: list[IPv4Network] = Field(min_length=1, max_length=32)


CONFIG_ONLY_MANAGEMENT = {
    "iosxr": XrManagementAddress,
    "iosxe": XeManagementAddress,
    "eos": EosManagementAddress,
}


class DeviceIntent(Contract):
    schema_version: Literal[1] = 1
    device: DeviceIdentity
    management: ManagementAddress | XrManagementAddress | XeManagementAddress | EosManagementAddress
    software: SoftwareIntent | None = None
    ztp_enabled: Literal[True]
    initial_configuration: InitialConfiguration | None = None

    @model_validator(mode="after")
    def platform_contract(self):
        if self.device.platform in CONFIG_ONLY_MANAGEMENT:
            if (
                not isinstance(self.management, CONFIG_ONLY_MANAGEMENT[self.device.platform])
                or self.software is not None
            ):
                label = CONFIG_ONLY_LABELS[self.device.platform][1]
                raise ValueError(label + " supports default-VRF configuration only; omit software")
        elif self.device.platform == "nxos":
            if type(self.management) is not ManagementAddress or self.software is None:
                raise ValueError("NX-OS requires mgmt0 and software intent")
        else:
            raise ValueError("Unsupported platform")
        return self


class CompatibilityProfile(Contract):
    id: Token
    model: Token
    source_versions: list[Token] = Field(min_length=1)
    target_version: Token
    image_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.bin$", max_length=200)]
    image_checksum: Digest
    image_size_bytes: int = Field(gt=0)
    # Planning support is not hardware qualification or authorization to install.
    hardware_qualified: bool = False
    replay_method: Literal["scheduled-config-exit"] | None = None
    install_method: Literal["poap-install-no-reload"] | None = None
    upgrade_path_approved: bool = False


class ConfigArtifact(Contract):
    sha256: Digest
    size_bytes: int = Field(gt=0, le=262144)


class DeviceEvent(Contract):
    event_id: Annotated[str, Field(pattern=r"^[a-f0-9-]{36}$")]
    event: Literal[
        "CONFIG_STAGED", "IMAGE_VERIFIED", "CONFIG_APPLYING", "CONFIG_COMMITTED", "FAILED"
    ]
    config_sha256: Digest


class Manifest(Contract):
    allow_newer_version: bool = False
    source_validation_enabled: bool = True
    validation_policy: Literal["ssh", "manual"] = "ssh"
    schema_version: Literal[1] = 1
    mode: Literal["planning-only", "configuration-only", "upgrade-and-configure"] = "planning-only"
    execution_enabled: bool = False
    actions: tuple[Literal["download-image", "install-image", "stage-config"], ...] = ()
    device_id: str
    serial_number: str
    intent_hash: Digest
    compatibility_profile: CompatibilityProfile
    target: SoftwareIntent
    management: ManagementAddress
    upgrade_required: bool
    bootstrap_revision: str = "not-implemented"
    template_revision: str = "not-implemented"
    config: ConfigArtifact | None = None

    @model_validator(mode="after")
    def execution_contract(self):
        if self.mode == "planning-only":
            if self.execution_enabled or self.actions or self.config is not None:
                raise ValueError("Planning manifests cannot execute")
        elif self.mode == "upgrade-and-configure":
            expected = (
                ("download-image", "install-image", "stage-config")
                if self.upgrade_required
                else ("stage-config",)
            )
            if not self.execution_enabled or self.config is None or self.actions != expected:
                raise ValueError("Invalid upgrade execution manifest")
            if (
                self.compatibility_profile.install_method != "poap-install-no-reload"
                or not self.compatibility_profile.upgrade_path_approved
            ):
                raise ValueError("Upgrade path must be explicitly approved")
        elif (
            not self.execution_enabled
            or self.actions != ("stage-config",)
            or self.upgrade_required
            or self.config is None
        ):
            raise ValueError("Configuration-only requires same-image config replay")
        return self


class ConfigOnlyManifest(Contract):
    schema_version: Literal[2] = 2
    mode: Literal["configuration-only"] = "configuration-only"
    validation_policy: Literal["manual"] = "manual"
    execution_enabled: Literal[True] = True
    actions: tuple[Literal["apply-config"], ...] = ("apply-config",)
    device_id: str
    serial_number: str
    intent_hash: Digest
    upgrade_required: Literal[False] = False
    bootstrap_revision: str
    template_revision: str
    config: ConfigArtifact

    @model_validator(mode="after")
    def config_only(self):
        if self.actions != ("apply-config",):
            raise ValueError("Configuration-only platforms support only apply-config")
        return self


class XrManifest(ConfigOnlyManifest):
    platform: Literal["iosxr"] = "iosxr"
    management: XrManagementAddress


class XeManifest(ConfigOnlyManifest):
    platform: Literal["iosxe"] = "iosxe"
    management: XeManagementAddress


class EosManifest(ConfigOnlyManifest):
    platform: Literal["eos"] = "eos"
    management: EosManagementAddress


def parse_manifest(value):
    if value.get("platform") == "iosxr":
        return XrManifest.model_validate(value)
    if value.get("platform") == "iosxe":
        return XeManifest.model_validate(value)
    if value.get("platform") == "eos":
        return EosManifest.model_validate(value)
    return Manifest.model_validate(value)


class RegistrationResponse(Contract):
    provisioning_id: str
    state: str = "AUTHORIZED"
    plan_hash: Digest
    manifest: Manifest | XrManifest | XeManifest | EosManifest
    status_token: str
    status_token_expires_at: int


class StatusResponse(Contract):
    validation_policy: Literal["ssh", "manual"] = "ssh"
    failure_reason: str | None = None
    validation: dict | None = None
    provisioning_id: str
    state: str
    plan_hash: Digest
    mode: Literal["planning-only", "configuration-only", "upgrade-and-configure"] = "planning-only"
