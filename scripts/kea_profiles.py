"""Generate complete editable IPv4 Kea lab profiles; no network mutation."""

import json
import struct
from pathlib import Path


def secure_options(url: str, ntp: str | None = None) -> str:
    # Cisco option 43: one-byte suboption code, two-byte length, network byte order.
    payload = bytes([1]) + struct.pack("!H", 1) + bytes([1])
    encoded = url.encode("ascii")
    payload += bytes([2]) + struct.pack("!H", len(encoded)) + encoded
    if ntp:
        from ipaddress import IPv4Address

        payload += bytes([3]) + struct.pack("!H", 4) + IPv4Address(ntp).packed
    if len(payload) > 255:
        raise ValueError("Example uses a single DHCP option; keep payload <=255 bytes")
    return ":".join(f"{b:02x}" for b in payload)


def configuration(mode, interface="eth1", server="192.0.2.2"):
    options = [
        {"name": "routers", "data": "192.0.2.1"},
        {"name": "domain-name-servers", "data": "192.0.2.1"},
    ]
    if mode == "secure-https":
        options.append(
            {
                "name": "cisco-poap",
                "csv-format": False,
                "always-send": True,
                "data": secure_options(f"https://{server}/bootstrap/poap.py", "192.0.2.1"),
            }
        )
    else:
        options.extend(
            [
                {
                    "name": "tftp-server-name",
                    "data": ("http://" + server if mode == "legacy-http" else server),
                    "always-send": True,
                },
                {
                    "name": "boot-file-name",
                    "data": ("bootstrap/poap.py" if mode == "legacy-http" else "poap.py"),
                    "always-send": True,
                },
            ]
        )
    result = {
        "Dhcp4": {
            "interfaces-config": {"interfaces": [interface]},
            "lease-database": {
                "type": "memfile",
                "persist": True,
                "name": "/var/lib/kea/kea-leases4.csv",
            },
            "valid-lifetime": 3600,
            "renew-timer": 900,
            "rebind-timer": 1800,
            "subnet4": [
                {
                    "id": 1,
                    "subnet": "192.0.2.0/24",
                    "interface": interface,
                    "pools": [{"pool": "192.0.2.100 - 192.0.2.199"}],
                    "option-data": options,
                }
            ],
            "loggers": [
                {"name": "kea-dhcp4", "output-options": [{"output": "stdout"}], "severity": "INFO"}
            ],
        }
    }

    if mode == "secure-https":
        result["Dhcp4"]["option-def"] = [
            {
                "name": "cisco-poap",
                "code": 43,
                "type": "binary",
                "space": "dhcp4",
                "encapsulate": "",
            }
        ]
    return result


if __name__ == "__main__":
    for mode in ("legacy-http", "legacy-tftp", "secure-https"):
        data = json.dumps(configuration(mode), indent=2) + "\n"
        Path(f"deploy/kea/profiles/{mode}.json").write_text(data)
        if mode == "legacy-http":
            Path("deploy/kea/kea-dhcp4.json").write_text(data)
