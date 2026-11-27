# InfraHub inventory

Set `ZTP_INVENTORY_PROVIDER=infrahub` to use the read-only GraphQL provider.
It supports the `DcimDevice` schema plus the additive extension in
[ztp-schema.json](../examples/infrahub/ztp-schema.json). Schema customization is
explicit; the controller never loads schemas or performs mutations at runtime.

## Connection

```dotenv
ZTP_INVENTORY_PROVIDER=infrahub
ZTP_INFRAHUB_URL=http://192.0.2.50:8000
ZTP_INFRAHUB_TOKEN=<read-only-api-token>
ZTP_INFRAHUB_BRANCH=main
ZTP_INFRAHUB_PLATFORM=cisco_nxos
ZTP_INFRAHUB_VERIFY_SSL=false
ZTP_INFRAHUB_ALLOW_HTTP=true
# Platform names must match InfraHub DcimPlatform names exactly:
ZTP_XR_PLATFORM_SLUG=cisco_iosxr
ZTP_XE_PLATFORM_SLUG=cisco_iosxe
ZTP_EOS_PLATFORM_SLUG=arista_eos
```

Use the reachable LAN address of the InfraHub host. `localhost` inside a
container means that container, and `host.docker.internal` does not resolve on a
Linux Docker host by default. A token is required even when InfraHub allows
anonymous reads; create a read-only API token in InfraHub.

Platform names are compared exactly. Many InfraHub datasets use names such as
`cisco_xr`, `cisco-iosxr`, `cisco_xe` or `cisco-iosxe`; set the
`ZTP_XR_PLATFORM_SLUG`/`ZTP_XE_PLATFORM_SLUG` values to the names your devices use
(these settings are shared with NetBox, which uses only one provider at a time).
HTTP and disabled certificate verification are
explicit controlled-network options. These variables are independent of
`DUCKCLI_CORE_INFRAHUB_*`; use the `ZTP_INFRAHUB_*` names above.
The Lite Compose file supports them directly. Keep API tokens out of Git.

## One-time schema extension

The supplied extension adds six optional attributes to existing `DcimDevice`
objects and does not replace existing attributes or relationships:

| Attribute | Meaning |
| --- | --- |
| `ztp_enabled` | Explicit authorization; defaults to false |
| `ztp_gateway` | Final management gateway |
| `ztp_target_version` | Intended NX-OS version |
| `ztp_image_name` | Filename matching the image catalog |
| `ztp_image_sha256` | 64-character lowercase SHA-256, no whitespace |
| `ztp_initial_configuration` | Optional JSON for standard template DNS/NTP/SSH sources |

Use a schema-authorized account to check and load this extension through InfraHub's
schema tools. The REST schema API accepts `{"schemas": [<extension document>]}` at
`/api/schema/check` and `/api/schema/load`, with `?branch=<branch>` when needed.
The runtime API token only needs read access. Readiness checks the schema extension;
missing fields or GraphQL errors make inventory unavailable.

## Device mapping

- `serial.value` is the chassis serial and must be unique. Use uppercase serials.
- `name.value` becomes the hostname; `status.value` must be `staged`.
- `platform.node.name.value` must match `ZTP_INFRAHUB_PLATFORM`.
- Model comes from device type `part_number.value`, falling back to `name.value`.
  Set it to the actual chassis PID, e.g. `N9K-C9300V`, rather than generic `nxos`.
  It must be a single token equal to the reported model, ignoring case: `C8000V`,
  `vEOS-lab` (not `Arista vEOS-lab`).
- `role.value` (optional `Dropdown`) selects a role template when the schema has
  it, for example `leaf` or `spine`. Values that are not lowercase slugs disable
  role templates for that device instead of failing registration.
- IOS XR, IOS XE and EOS leave `ztp_target_version`, `ztp_image_name` and
  `ztp_image_sha256` empty; the management interface is their real interface
  (`MgmtEth…`, `GigabitEthernet1`, `Management1`) in the default VRF.
- Avoid renaming a shared generic device type; create/select the appropriate type.
- Device-type manufacturer name is normalized to lowercase; NX-OS requires `Cisco`.
- `mgmt_interface.value` names the management interface (`mgmt0` for NX-OS). The
  primary address must be on an interface of that name owned by this device. An
  unassigned IP is rejected.
- Gateway must belong to the primary-IP subnet and differ from the device address.
- In Lite with `ZTP_CATALOG_SOURCE=auto`, the NX-OS plan comes from these
  attributes and the non-empty image file of that name in the image directory; no
  catalog is needed. With `catalog`, they must match a unique catalog profile.
- Revocations and changes are re-read on registration and artifact authorization.
- Registration failures name the invalid field (never its value), for example
  `InfraHub device identity is incomplete or invalid: model`.

Use the actual chassis serial and PID, an assigned management address and a
gateway on its subnet, plus the verified image SHA-256 and target release.
Enable ZTP only when the complete record (and image or catalog) is ready. Merely setting
`automation_enabled` does not authorize ZTP.

The dashboard shows registered InfraHub devices and registration diagnostics;
it does not bulk-list or edit InfraHub inventory. Unsupported schemas must be
mapped explicitly rather than inferred. Device deployment and upgrade behavior
remain shared with NetBox and local YAML.

Reference: [InfraHub GraphQL API](https://docs.infrahub.app/development-resources/graphql/overview).
