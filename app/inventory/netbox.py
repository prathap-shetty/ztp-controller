from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import ValidationError

from app.errors import InvalidIntent, InventoryDenied, InventoryUnavailable
from app.inventory.base import InventoryProvider
from app.models.contracts import (
    DeviceIdentity,
    DeviceIntent,
    InitialConfiguration,
    ManagementAddress,
    SoftwareIntent,
)


def identity(record: dict, platform_slug: str) -> DeviceIdentity:
    if not record.get("platform"):
        raise InvalidIntent("platform_missing", "NetBox device has no platform assigned")
    if record["platform"].get("slug") != platform_slug:
        raise InvalidIntent(
            "platform_mismatch", "NetBox platform does not match configured platform slug"
        )
    try:
        return DeviceIdentity(
            id=str(record["id"]),
            name=record["name"],
            serial_number=record["serial"].strip().upper(),
            vendor=record["device_type"]["manufacturer"]["slug"],
            platform=("nxos" if record["platform"]["slug"] == platform_slug else "unsupported"),
            model=record["device_type"]["model"].upper(),
            status=record["status"]["value"],
        )
    except (KeyError, TypeError, AttributeError, ValidationError) as exc:
        raise InvalidIntent() from exc


class NetBoxInventoryProvider(InventoryProvider):
    def __init__(
        self,
        base_url: str,
        token: str,
        platform_slug: str = "nxos",
        auth_scheme: str = "Token",
        transport=None,
        verify_ssl: bool = True,
        branch: str = "main",
    ):
        self.base_url = base_url.rstrip("/") + "/api/"
        self.platform_slug = platform_slug
        headers = {"Authorization": f"{auth_scheme} {token}", "Accept": "application/json"}
        if branch.strip() and branch.strip() != "main":
            headers["X-NetBox-Branch"] = branch.strip()
        self.client = httpx.Client(
            headers=headers,
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            transport=transport,
            verify=verify_ssl,
        )

    def _get(self, path: str, params=None) -> dict:
        url = urljoin(self.base_url, path)
        base, target = urlsplit(self.base_url), urlsplit(url)
        if (base.scheme, base.netloc) != (
            target.scheme,
            target.netloc,
        ) or not target.path.startswith(base.path):
            raise InventoryUnavailable()
        try:
            response = self.client.get(url, params=params)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Unexpected response")
            return result
        except (httpx.HTTPError, ValueError) as exc:
            raise InventoryUnavailable() from exc

    def get_device_by_serial(self, serial: str) -> DeviceIdentity:
        page = self._get("dcim/devices/", {"serial": serial, "limit": 100})
        records, visited = [], set()
        for _ in range(100):
            batch = page.get("results")
            if not isinstance(batch, list):
                raise InventoryUnavailable()
            records.extend(batch)
            if len(records) > 1:
                raise InventoryDenied()
            next_url = page.get("next")
            if not next_url:
                break
            if not isinstance(next_url, str) or next_url in visited:
                raise InventoryUnavailable()
            visited.add(next_url)
            page = self._get(next_url)
        else:
            raise InventoryUnavailable()
        if len(records) != 1:
            raise InventoryDenied("serial_not_found", "Chassis serial was not found in inventory")
        device = identity(records[0], self.platform_slug)
        if device.serial_number != serial:
            raise InventoryDenied()
        return device

    def get_device_intent(self, device_id: str) -> DeviceIntent:
        if not device_id.isdecimal():
            raise InvalidIntent()
        record = self._get(f"dcim/devices/{device_id}/")
        device = identity(record, self.platform_slug)
        try:
            if device.id != device_id:
                raise InvalidIntent()
            context = record["config_context"]
            if context["provisioning"]["ztp_enabled"] is not True:
                raise InventoryDenied("ztp_disabled", "ZTP is not enabled for this device")
            ztp = context["ztp"]
            import re

            if not isinstance(ztp["image"]["sha256"], str) or not re.fullmatch(
                r"[a-f0-9]{64}", ztp["image"]["sha256"]
            ):
                raise InvalidIntent(
                    "invalid_image_checksum",
                    "Image SHA-256 must be 64 lowercase hex characters without whitespace",
                )
            management = ztp["management"]
            primary = record["primary_ip4"]
            ip_id = str(primary["id"])
            if not ip_id.isdecimal():
                raise InvalidIntent()
            ip_record = self._get(f"ipam/ip-addresses/{ip_id}/")
            assigned = ip_record["assigned_object"]
            if (
                ip_record["assigned_object_type"] != "dcim.interface"
                or str(assigned["device"]["id"]) != device_id
                or assigned["name"] != "mgmt0"
                or primary["address"] != ip_record["address"]
            ):
                raise InvalidIntent()
            return DeviceIntent(
                device=device,
                ztp_enabled=True,
                initial_configuration=(
                    InitialConfiguration.model_validate(ztp["initial_configuration"])
                    if "initial_configuration" in ztp
                    else None
                ),
                management=ManagementAddress(
                    address=ip_record["address"],
                    gateway=management["gateway"],
                    vrf=management["vrf"],
                    interface=management["interface"],
                ),
                software=SoftwareIntent(
                    target_version=ztp["target_nxos"],
                    image_name=ztp["image"]["filename"],
                    image_checksum=ztp["image"]["sha256"],
                ),
            )
        except KeyError as exc:
            raise InvalidIntent(
                "missing_ztp_field", "Required ZTP context or management field is missing"
            ) from exc
        except (TypeError, AttributeError, ValidationError) as exc:
            raise InvalidIntent(
                "invalid_ztp_context", "ZTP context or primary management IP is invalid"
            ) from exc

    def check_ready(self) -> None:
        self._get("dcim/devices/", {"limit": 1, "exclude": "config_context"})

    def close(self) -> None:
        self.client.close()
