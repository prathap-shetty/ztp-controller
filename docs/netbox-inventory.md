# NetBox inventory for ZTP Lite

This guide lists everything ZTP Lite needs in NetBox to provision Cisco NX-OS,
IOS XE, IOS XR and Arista EOS devices. The controller only **reads** NetBox; it
never creates or edits records. A device registers itself during ZTP, the
controller looks it up by chassis serial, checks the record against what the
device reported, and renders its configuration from the record.

Values in `code` must match exactly unless the text says otherwise. Serials,
addresses and names below are placeholders; use your own.

## 1. Controller settings

In `.env.lite` on the controller host:

```dotenv
ZTP_INVENTORY_PROVIDER=netbox
ZTP_NETBOX_URL=https://192.0.2.50          # reachable from the controller host
ZTP_NETBOX_TOKEN=<read-only API token>
ZTP_NETBOX_BRANCH=main                     # optional: a branch schema ID
ZTP_CATALOG_SOURCE=auto                    # NX-OS plans come from NetBox

# Platform slugs: each must equal the NetBox platform slug exactly.
ZTP_NETBOX_PLATFORM_SLUG=cisco_nxos
ZTP_XR_PLATFORM_SLUG=cisco_iosxr
ZTP_XE_PLATFORM_SLUG=cisco_iosxe
ZTP_EOS_PLATFORM_SLUG=arista_eos

# Turn on the configuration-only platforms you use.
ZTP_XR_ENABLED=true
ZTP_XE_ENABLED=true
ZTP_EOS_ENABLED=true
```

Create the token under your NetBox user profile; it only needs read access to
devices, interfaces and IP addresses. Recreate the API and worker containers after
changing these values. `http://<controller>/ready` returns `{"status":"ready"}`
when NetBox is reachable with the token.

## 2. Objects to create once

### Manufacturers

| Name | Slug |
|---|---|
| Cisco | `cisco` |
| Arista | `arista` |

The **slug** is compared with the vendor the device reports. Any other slug fails
with `vendor_mismatch`.

### Platforms

| Platform | Slug (default) | Manufacturer | Setting |
|---|---|---|---|
| Cisco NX-OS | `cisco_nxos` | Cisco | `ZTP_NETBOX_PLATFORM_SLUG` |
| Cisco IOS XR | `cisco_iosxr` | Cisco | `ZTP_XR_PLATFORM_SLUG` |
| Cisco IOS XE | `cisco_iosxe` | Cisco | `ZTP_XE_PLATFORM_SLUG` |
| Arista EOS | `arista_eos` | Arista | `ZTP_EOS_PLATFORM_SLUG` |

If your NetBox already uses other slugs (for example `cisco-nxos`), set the
matching `ZTP_*_PLATFORM_SLUG` instead of renaming platforms. The NX-OS default in
Compose is `nxos` when the variable is unset, so always set it.

### Device types

The device type **Model** must equal the model the device reports, ignoring case,
as a single word with no spaces. Use the chassis PID from `show inventory` (EOS:
`modelName` from `show version`).

| Platform | Where the device reports its model | Example model (lab-verified) |
|---|---|---|
| NX-OS | `show inventory`, chassis `PID` | `N9K-C9500V` (Nexus 9000v 9500), `N9K-C9300V` |
| IOS XE | `show inventory`, chassis `PID` | `C8000V` (Catalyst 8000V) |
| IOS XR | `show inventory`, `Rack 0` or `Chassis` `PID` | `R-IOSXRV9000-CC` (XRv 9000) |
| Arista EOS | `show version`, `modelName` | `vEOS-lab` |

`Arista vEOS-lab` (with a space) is rejected; `vEOS-lab` works. Use the real
PID for hardware, for example `N9K-C93180YC-FX3`.

### Device roles (optional)

A role slug such as `leaf`, `spine` or `ext-router` selects an extra role
template. See *Role-based configuration* in the
[user guide](ztp-lite-user-guide.md). A device without a role gets only the
common configuration.

## 3. Every device

| Field | Value |
|---|---|
| Name | Becomes the hostname: letters, digits, `.` and `-`, up to 63 characters |
| Device type | From section 2 (sets manufacturer and model) |
| Platform | From section 2 |
| Serial number | The chassis serial the device reports (see below), uppercase |
| Status | `Staged` (`staged`). Any other status fails with `device_not_staged` |
| Role | Optional, see section 2 |
| Interface | The management interface, named exactly as on the device (per platform below) |
| Primary IPv4 | The **final** management address, assigned to that interface of this device. Keep it outside the DHCP pool |
| Config context | Per platform below |

Where each platform reports its serial:

| Platform | Source |
|---|---|
| NX-OS | `show inventory`, `NAME: "Chassis"` `SN` |
| IOS XE | `show inventory`, `NAME: "Chassis"` `SN` (`show version` "Processor board ID" on a C8000V) |
| IOS XR | `show inventory`, `NAME: "Rack 0"` `SN` |
| Arista EOS | `show version`, `serialNumber`; for vEOS/cEOS with an empty serial, the system MAC without separators in uppercase |

If unsure, let the device run ZTP once: **Recent registration failures** in the
dashboard shows the serial and model it reported.

## 4. Per platform

### Cisco NX-OS

- Interface: `mgmt0`, VRF `management`.
- Software intent (`target_nxos` and `image`) is **optional**:
  - **Omit both** to configure the switch only, whatever release it runs. No image
    file, catalog profile or release check is needed.
  - **Give both** to check the release and upgrade when needed (below). The image
    file must then exist, non-empty, in the controller's `images/` folder with
    the same filename.
  - Giving only one of them is rejected with `nxos_software_incomplete`.

Configuration only:

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "management": {"interface": "mgmt0", "vrf": "management", "gateway": "192.0.2.1"}
  }
}
```

With release check and upgrade:

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "target_nxos": "10.4(3)",
    "image": {
      "filename": "nxos64-cs.10.4.3.F.bin",
      "sha256": "<64 lowercase hex characters>"
    },
    "management": {"interface": "mgmt0", "vrf": "management", "gateway": "192.0.2.1"}
  }
}
```

- `target_nxos` is compared with the version the switch reports
  (`show version | json | include nxos_ver_str`). If they are equal, the switch
  skips installation and only applies configuration. An NXOSv on `10.4(3)F`
  reports `10.4(3)`, and that is the value to use.
- An equal or newer *numeric* release also skips installation. Unknown suffixes
  such as `F` are never ordered.
- Upgrading from a different release needs `ZTP_SKIP_SOURCE_VALIDATION=true`.
- `sha256` must be exactly 64 lowercase hex characters with no spaces.

### Cisco IOS XE (configuration only)

- Interface: the one that receives the ZTP DHCP lease, for example
  `GigabitEthernet1`, VRF `default`. `Mgmt-vrf` is not supported.

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "management": {"interface": "GigabitEthernet1", "vrf": "default", "gateway": "192.0.2.1"}
  }
}
```

### Cisco IOS XR (configuration only)

- Interface: `MgmtEth0/RP0/CPU0/0` (full name, not `Mg0/…` and not `mgmt0`),
  VRF `default`.
- Lab-verified on XRv 9000 25.1.1.

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "management": {"interface": "MgmtEth0/RP0/CPU0/0", "vrf": "default", "gateway": "192.0.2.1"}
  }
}
```

### Arista EOS (configuration only)

- Interface: `Management1`, VRF `default`. The device must reach the ZTP network
  through `Management1`, not a front-panel `Ethernet` port.

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "management": {"interface": "Management1", "vrf": "default", "gateway": "192.0.2.1"}
  }
}
```

IOS XE, IOS XR and EOS contexts must not contain `target_nxos`, `image` or
`software`; the controller rejects them with `xr_configuration_only`,
`xe_configuration_only` or `eos_configuration_only`.

## 5. Config context merging

NetBox merges **every** config context that matches a device with its local
context data. A context with no platform, role or site filter applies to all
devices, so NX-OS keys in it (`target_nxos`, `image`) break IOS XE, IOS XR and
EOS devices.

- Scope shared contexts by **platform** (for example NX-OS image defaults only to
  `cisco_nxos`), or use local config context data per device.
- Check the merged result on the device's **Config Context** tab before enabling
  ZTP.
- An empty `vrf` in a broader context can override a device value and fail with
  `invalid_ztp_context: vrf`.

## 6. DHCP

On a subnet shared by several platforms, use `examples/mixed-platform/kea.json`.
It sends each platform its own bootstrap:

| Platform | Matched on | Bootstrap |
|---|---|---|
| NX-OS | option 77 `Cisco-POAP` | `bootstrap/nxos.py` |
| IOS XE | option 60 `ciscopnp` | `bootstrap/iosxe.py` |
| IOS XR | option 77 `exr-config` or `xr-config` | `bootstrap/iosxr.py` |
| Arista EOS | option 60 starting with `Arista;` | `bootstrap/eos.py` |

Make sure no other DHCP server, such as a hypervisor's built-in one, answers on
the provisioning network.

## 7. Troubleshooting

The dashboard's **Recent registration failures** shows the reported serial,
model and platform, the failed step and a reason. Common ones:

| Code | Meaning | Fix in NetBox |
|---|---|---|
| `serial_not_found` | No device with this serial | Add the device, or correct the serial |
| `not_eligible` at `inventory_lookup` | More than one device with this serial | Remove the duplicate |
| `platform_missing` | Device has no platform | Set the platform |
| `platform_mismatch` | Platform slug not one of the configured slugs, or wrong OS | Set the right platform, or the matching `ZTP_*_PLATFORM_SLUG` |
| `vendor_mismatch` | Manufacturer slug differs from `cisco` or `arista` | Fix the manufacturer slug |
| `model_mismatch` | Device type model differs from the reported model | Set the model to the reported PID or `modelName` |
| `invalid_intent: model` | Model contains a space or invalid character | Use one word, for example `vEOS-lab` |
| `device_not_staged` | Status is not Staged | Set status to Staged |
| `missing_ztp_field: provisioning` | No ZTP config context | Add the context from section 4 |
| `ztp_disabled` | `provisioning.ztp_enabled` is not `true` | Set it to `true` |
| `invalid_ztp_context: vrf` | Wrong or empty VRF | `management` for NX-OS, `default` for the others |
| `invalid_ztp_context: unknown` | Often no primary IPv4 set on the device | Set the primary IPv4 |
| `xr/xe/eos_configuration_only` | NX-OS software keys reached a config-only device | Remove them, or scope the shared context (section 5) |
| `invalid_management_interface` | The primary IPv4 is not on a device interface, is on another device, or is on an interface whose name differs from `ztp.management.interface` | Assign the primary IPv4 to the correctly named interface of this device |
| `nxos_software_incomplete` | NX-OS context has only one of `target_nxos` and `image` | Give both, or neither for configuration only |
| `invalid_image_checksum` | NX-OS SHA-256 malformed | 64 lowercase hex characters |
| `image_unavailable` | NX-OS image file missing or empty on the controller | Copy the image into `images/` |
| `source_validation_required` | NX-OS running a different release with source checks on | Match `target_nxos` to the running release, or allow the bypass |
| `attempt_conflict` | An attempt already exists for this device | Reset the device in the dashboard, then rerun ZTP |
| `inventory_unavailable` | NetBox unreachable, or the token was rejected | Check `ZTP_NETBOX_URL`, the token and the network path |

After a successful run with `ZTP_LITE_VERIFICATION=true`, the device shows as
**Verified** once ping, SSH, identity and configuration checks pass.
