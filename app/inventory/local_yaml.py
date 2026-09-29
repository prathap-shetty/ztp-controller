"""Read-only local inventory; reload on every access so revocations take effect."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import StrictBool, ValidationError, field_validator, model_validator
from yaml.events import AliasEvent

from app.errors import InvalidIntent, InventoryDenied, InventoryUnavailable
from app.inventory.base import InventoryProvider
from app.models.contracts import Contract, DeviceIdentity, DeviceIntent


class LocalDevice(DeviceIntent):
    ztp_enabled: StrictBool

    @field_validator("device")
    @classmethod
    def normalize_identity(cls, value: DeviceIdentity):
        return value.model_copy(
            update={
                "serial_number": value.serial_number.upper(),
                "model": value.model.upper(),
            }
        )


class InventoryDocument(Contract):
    schema_version: Literal[1]
    devices: list[LocalDevice]

    @model_validator(mode="after")
    def unique_devices(self):
        for key in ("id", "serial_number"):
            values = [getattr(entry.device, key) for entry in self.devices]
            if len(set(values)) != len(values):
                raise ValueError("Duplicate device identity")
        return self


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(set(keys)) != len(keys):
            raise ValueError("Duplicate YAML key")
        return super().construct_mapping(node, deep=deep)


class LocalYamlInventoryProvider(InventoryProvider):
    def __init__(self, path: Path):
        self.path = path

    def _load(self):
        try:
            with self.path.open("rb") as handle:
                content = handle.read(1024 * 1024 + 1)
        except OSError:
            raise InventoryUnavailable() from None
        try:
            if len(content) > 1024 * 1024:
                raise ValueError("Inventory too large")
            if any(isinstance(event, AliasEvent) for event in yaml.parse(content)):
                raise ValueError("YAML aliases are not supported")
            return InventoryDocument.model_validate(yaml.load(content, Loader=UniqueLoader))
        except (yaml.YAMLError, ValidationError, ValueError, TypeError, RecursionError):
            raise InvalidIntent() from None

    def get_device_by_serial(self, serial: str) -> DeviceIdentity:
        matches = [
            r.device for r in self._load().devices if r.device.serial_number == serial.upper()
        ]
        if len(matches) != 1:
            raise InventoryDenied()
        return matches[0]

    def get_device_intent(self, device_id: str) -> DeviceIntent:
        matches = [r for r in self._load().devices if r.device.id == device_id]
        if len(matches) != 1 or not matches[0].ztp_enabled:
            raise InventoryDenied()
        return DeviceIntent.model_validate(matches[0].model_dump())

    def check_ready(self):
        self._load()

    def close(self):
        pass
