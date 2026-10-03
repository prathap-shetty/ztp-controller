"""Read-only InfraHub DcimDevice inventory with explicit ZTP schema extension."""

import httpx
from pydantic import ValidationError

from app.errors import InvalidIntent, InventoryDenied, InventoryUnavailable
from app.inventory.base import InventoryProvider
from app.models.contracts import DeviceIdentity, DeviceIntent

IDENTITY = """id name { value } serial { value } status { value }
platform { node { name { value } } }
device_type { node { name { value } part_number { value }
manufacturer { node { name { value } } } } }"""
INTENT = (
    IDENTITY
    + """
mgmt_interface { value }
primary_address { node { address { value }
interface { node { ... on DcimInterface { name { value } device { node { id } } } } } } }
ztp_enabled { value } ztp_gateway { value } ztp_target_version { value }
ztp_image_name { value } ztp_image_sha256 { value } ztp_initial_configuration { value }
"""
)


def value(node, name):
    return node[name]["value"]


class InfraHubInventoryProvider(InventoryProvider):
    def __init__(
        self, url, token, branch="main", platform="cisco_nxos", verify_ssl=True, transport=None
    ):
        self.platform = platform
        self.endpoint = url.rstrip("/") + "/graphql/" + branch
        self.client = httpx.Client(
            headers={"X-INFRAHUB-KEY": token},
            verify=verify_ssl,
            transport=transport,
            timeout=10,
            follow_redirects=False,
        )

    def _query(self, query, variables=None):
        try:
            with self.client.stream(
                "POST", self.endpoint, json={"query": query, "variables": variables or {}}
            ) as r:
                r.raise_for_status()
                body = bytearray()
                for chunk in r.iter_bytes():
                    body.extend(chunk)
                    if len(body) > 1048576:
                        raise ValueError("Oversized response")
            import json

            payload = json.loads(body)
            if payload.get("errors") or not isinstance(payload.get("data"), dict):
                raise ValueError("GraphQL failed")
            return payload["data"]
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise InventoryUnavailable() from None

    def _one(self, selector, variable, fields):
        query = (
            "query Device($value: "
            + ("String!" if selector == "serial__value" else "[ID]!")
            + ") { DcimDevice("
            + selector
            + ": $value, limit: 2) { edges { node { "
            + fields
            + " } } } }"
        )
        data = self._query(query, {"value": variable})
        try:
            rows = data["DcimDevice"]["edges"]
            if len(rows) != 1:
                raise InventoryDenied(
                    "serial_not_unique", "Inventory lookup must return exactly one device"
                )
            return rows[0]["node"]
        except (KeyError, TypeError):
            raise InventoryUnavailable() from None

    def _identity(self, node):
        try:
            platform = node["platform"]["node"]
            if not platform:
                raise InvalidIntent("platform_missing", "InfraHub device has no platform assigned")
            if value(platform, "name") != self.platform:
                raise InvalidIntent(
                    "platform_mismatch", "InfraHub platform does not match configured platform"
                )
            device_type = node["device_type"]["node"]
            return DeviceIdentity(
                id=node["id"],
                name=value(node, "name"),
                serial_number=value(node, "serial").strip().upper(),
                status=value(node, "status"),
                model=(value(device_type, "part_number") or value(device_type, "name")).upper(),
                vendor=value(device_type["manufacturer"]["node"], "name").lower(),
                platform="nxos",
            )
        except (KeyError, TypeError, AttributeError, ValidationError):
            raise InvalidIntent(
                "invalid_identity", "InfraHub device identity is incomplete or invalid"
            ) from None

    def get_device_by_serial(self, serial):
        identity = self._identity(self._one("serial__value", serial.upper(), IDENTITY))
        if identity.serial_number != serial.upper():
            raise InventoryDenied("serial_mismatch", "Returned chassis serial does not match")
        return identity

    def get_device_intent(self, device_id):
        node = self._one("ids", [device_id], INTENT)
        identity = self._identity(node)
        try:
            if identity.id != device_id:
                raise InvalidIntent()
            if value(node, "ztp_enabled") is not True:
                raise InventoryDenied("ztp_disabled", "ZTP is not enabled for this device")
            ip = node["primary_address"]["node"]
            interface = ip["interface"]["node"]
            if (
                value(node, "mgmt_interface") != "mgmt0"
                or value(interface, "name") != "mgmt0"
                or interface["device"]["node"]["id"] != device_id
            ):
                raise InvalidIntent(
                    "invalid_management_interface", "Primary IP must belong to this device mgmt0"
                )
            return DeviceIntent.model_validate(
                {
                    "device": identity.model_dump(),
                    "ztp_enabled": True,
                    "management": {
                        "address": value(ip, "address"),
                        "gateway": value(node, "ztp_gateway"),
                        "interface": "mgmt0",
                        "vrf": "management",
                    },
                    "software": {
                        "target_version": value(node, "ztp_target_version"),
                        "image_name": value(node, "ztp_image_name"),
                        "image_checksum": value(node, "ztp_image_sha256"),
                    },
                    "initial_configuration": value(node, "ztp_initial_configuration"),
                }
            )
        except (KeyError, TypeError, AttributeError, ValidationError):
            raise InvalidIntent(
                "invalid_ztp_context", "InfraHub ZTP fields or management address are invalid"
            ) from None

    def check_ready(self):
        # Verify the extension exists without requiring a populated device.
        self._query(
            "query { DcimDevice(limit: 1) { edges { node { id ztp_enabled { value } } } } }"
        )

    def close(self):
        self.client.close()
