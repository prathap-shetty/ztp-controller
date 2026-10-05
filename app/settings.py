from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZTP_", extra="ignore")
    database_url: SecretStr
    infrahub_url: str = ""
    infrahub_token: SecretStr | None = None
    infrahub_token_file: Path | None = None
    infrahub_verify_ssl: bool = True
    infrahub_allow_http: bool = False
    infrahub_branch: str = "main"
    infrahub_platform: str = "cisco_nxos"
    netbox_url: str = ""
    netbox_branch: str = "main"
    netbox_verify_ssl: bool = True
    netbox_token: SecretStr | None = None
    netbox_token_file: Path | None = None
    netbox_auth_scheme: Literal["Token", "Bearer"] = "Token"
    netbox_platform_slug: str = "nxos"
    image_directory: Path = Path("images")
    upgrade_validation_delay_seconds: int = 1800
    upgrade_deadline_seconds: int = 7200
    catalog_path: Path = Path("catalog/profiles.json")
    inventory_provider: Literal["netbox", "local-yaml", "infrahub"] = "netbox"
    local_inventory_path: Path = Path("inventory/devices.yaml")
    allow_http_netbox: bool = False
    execution_mode: Literal["planning-only", "configuration-only", "upgrade-and-configure"] = (
        "planning-only"
    )
    allow_unqualified_lab: bool = False
    template_path: Path = Path("templates/cisco/nxos_initial.j2")
    bootstrap_path: Path = Path("poap/cisco/poap.py")
    dashboard_token: SecretStr | None = None
    dashboard_secure_cookie: bool = True
    lite_mode: bool = False
    allow_newer_version: bool = False
    skip_source_validation: bool = False
    admin_password: SecretStr | None = None
    ssh_public_key_file: Path | None = None
    ssh_private_key_file: Path | None = None
    ssh_known_hosts_file: Path | None = None
    ssh_username: str = "ztp"
    validation_delay_seconds: int = 90
    validation_deadline_seconds: int = 1800
    validation_retry_seconds: int = 30
    validation_max_attempts: int = 30
    validation_lease_seconds: int = 180
    status_token_ttl_seconds: int = 3600
    registration_limit_per_minute: int = 60

    @field_validator("netbox_branch", "infrahub_branch", mode="before")
    @classmethod
    def default_branch(cls, value):
        return value.strip() or "main" if isinstance(value, str) else value

    @field_validator("netbox_branch")
    @classmethod
    def validate_netbox_branch(cls, value):
        import re

        if value != "main" and not re.fullmatch(r"[A-Za-z0-9]{8}", value):
            raise ValueError("NetBox branch must be main or an eight-character schema ID")
        return value

    @model_validator(mode="after")
    def validate_configuration(self):
        if self.dashboard_token and len(self.dashboard_token.get_secret_value()) < 24:
            raise ValueError("Dashboard token must contain at least 24 characters")
        if self.inventory_provider == "infrahub":
            import re

            url = urlsplit(self.infrahub_url)
            if (
                url.scheme not in {"https", "http"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in {"", "/"}
            ):
                raise ValueError("InfraHub URL must be an HTTP(S) origin")
            if url.scheme == "http" and not self.infrahub_allow_http:
                raise ValueError("HTTP InfraHub requires infrahub_allow_http")
            if bool(self.infrahub_token) == bool(self.infrahub_token_file):
                raise ValueError("Configure exactly one InfraHub token source")
            if not re.fullmatch(r"[A-Za-z0-9_-]+", self.infrahub_branch):
                raise ValueError("Invalid InfraHub branch name")
        if self.inventory_provider == "netbox":
            url = urlsplit(self.netbox_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "netbox_url must be an HTTP(S) base URL without credentials or query"
                )
            if url.scheme != "https" and not self.allow_http_netbox:
                raise ValueError("HTTP NetBox requires explicit allow_http_netbox for a local lab")
            if not self.netbox_token and not self.netbox_token_file:
                raise ValueError("A read-only NetBox token or token file is required")
            if self.netbox_token and self.netbox_token_file:
                raise ValueError("Configure only one NetBox token source")
        if not 60 <= self.status_token_ttl_seconds <= 86400:
            raise ValueError("Token TTL must be between 60 and 86400 seconds")
        if not 1 <= self.registration_limit_per_minute <= 10000:
            raise ValueError("Registration rate must be between 1 and 10000 per minute")
        if self.skip_source_validation and not self.lite_mode:
            raise ValueError("Source validation bypass requires Lite mode")
        if self.lite_mode:
            import re

            if not self.admin_password or not re.fullmatch(
                r"[A-Za-z0-9!@%_+=.-]{8,128}", self.admin_password.get_secret_value()
            ):
                raise ValueError("Lite requires an 8-128 character single-token admin password")
        if self.execution_mode != "planning-only" and not self.lite_mode:
            if not self.ssh_public_key_file:
                raise ValueError("Configuration-only mode requires the SSH public key file")
            if not self.ssh_username.isalnum() or len(self.ssh_username) > 32:
                raise ValueError("SSH username must be alphanumeric, at most 32 characters")
        if not (1 <= self.validation_delay_seconds < self.validation_deadline_seconds <= 86400):
            raise ValueError("Invalid validation delay/deadline")
        if not (
            1 <= self.validation_retry_seconds <= 600 and 1 <= self.validation_max_attempts <= 100
        ):
            raise ValueError("Invalid validation retry budget")
        if not 1 <= self.upgrade_validation_delay_seconds < self.upgrade_deadline_seconds <= 86400:
            raise ValueError("Invalid upgrade validation budget")
        if self.validation_lease_seconds < 180:
            raise ValueError("Validation lease must cover bounded SSH and inventory calls")
        return self

    def token(self) -> str:
        value = (
            self.netbox_token_file.read_text().strip()
            if self.netbox_token_file
            else self.netbox_token.get_secret_value()
        )
        if not value or any(c.isspace() for c in value):
            raise ValueError("NetBox token must be nonempty without whitespace")
        return value
