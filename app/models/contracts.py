from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Token = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.():-]+$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ObservedDevice(Contract):
    serial_number: Token
    vendor: Literal["cisco"]
    platform: Literal["nxos"]
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


class DeviceIdentity(Contract):
    id: Token
    name: Token
    serial_number: Token
    vendor: Token
    platform: Token
    model: Token
    status: Token


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


class SoftwareIntent(Contract):
    target_version: Token
    image_name: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.bin$", max_length=200)]
    image_checksum: Digest


class InitialConfiguration(Contract):
    dns_servers: list[IPv4Address] = Field(default_factory=list, max_length=3)
    ntp_servers: list[IPv4Address] = Field(default_factory=list, max_length=4)
    ssh_sources: list[IPv4Network] = Field(min_length=1, max_length=32)


class DeviceIntent(Contract):
    schema_version: Literal[1] = 1
    device: DeviceIdentity
    management: ManagementAddress
    software: SoftwareIntent
    ztp_enabled: Literal[True]
    initial_configuration: InitialConfiguration | None = None


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
    event: Literal["CONFIG_STAGED", "IMAGE_VERIFIED", "FAILED"]
    config_sha256: Digest


class Manifest(Contract):
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


class RegistrationResponse(Contract):
    provisioning_id: str
    state: str = "AUTHORIZED"
    plan_hash: Digest
    manifest: Manifest
    status_token: str
    status_token_expires_at: int


class StatusResponse(Contract):
    failure_reason: str | None = None
    validation: dict | None = None
    provisioning_id: str
    state: str
    plan_hash: Digest
    mode: Literal["planning-only", "configuration-only", "upgrade-and-configure"] = "planning-only"
