# InfraHub inventory

Set `ZTP_INVENTORY_PROVIDER=infrahub` to use the read-only GraphQL provider.
It supports the `DcimDevice` schema plus the additive extension in
[ztp-schema.json](../examples/infrahub/ztp-schema.json). Schema customization is
explicit; the controller never loads schemas or performs mutations at runtime.

## Connection

```dotenv
ZTP_INVENTORY_PROVIDER=infrahub
ZTP_INFRAHUB_URL=http://host.docker.internal:8000
ZTP_INFRAHUB_TOKEN=<read-only-api-token>
ZTP_INFRAHUB_BRANCH=main
ZTP_INFRAHUB_PLATFORM=cisco_nxos
ZTP_INFRAHUB_VERIFY_SSL=false
ZTP_INFRAHUB_ALLOW_HTTP=true
```

On Docker Desktop use `host.docker.internal` to reach InfraHub running on the
Mac; `localhost` inside a container means that container. For a Linux controller
use the reachable LAN address of the InfraHub host. HTTP and disabled verification
are explicit lab options; use HTTPS with verification for secured environments.
These settings are independent of `DUCKCLI_CORE_INFRAHUB_*`: copy the appropriate
values into the `ZTP_INFRAHUB_*` variables; the controller does not read DuckCLI's
configuration. Keep the token out of Git. Direct Python deployments may use
`ZTP_INFRAHUB_TOKEN_FILE` instead of the token environment variable (exactly one).

Lite and Mac Compose support these variables directly. The standard stack uses
`-f compose.yaml -f compose.infrahub.yaml` to remove the unrelated NetBox secret
requirement. Standard TLS/SSH requirements are unchanged.

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
  Avoid renaming a shared generic type; create/select the appropriate type.
- Device-type manufacturer name is normalized to lowercase; NX-OS requires `Cisco`.
- `mgmt_interface.value` must be `mgmt0`. The primary address must have a related
  interface named `mgmt0` owned by this device. An unassigned IP is rejected.
- Gateway must belong to the primary-IP subnet and differ from the device address.
- Image metadata and target version must match a unique local catalog profile.
- Revocations and changes are re-read on registration and artifact authorization.

For `dc1-pod1-ztp-leaf-1`, use chassis serial `93MK8XNKSEG`, model `N9K-C9300V`,
primary IP `192.168.20.50/24`, gateway `192.168.20.1`, and target `10.5(4)` (matching
your chosen catalog). Use the verified checksum of `nxos64-cs.10.5.4.M.bin`.
Enable ZTP only when the complete record and catalog are ready. Merely setting
`automation_enabled` does not authorize ZTP.

The dashboard shows registered InfraHub devices and registration diagnostics;
it does not bulk-list or edit InfraHub inventory. Unsupported schemas must be
mapped explicitly rather than inferred. Device deployment and upgrade behavior
remain shared with NetBox and local YAML.

Reference: [InfraHub GraphQL API](https://docs.infrahub.app/development-resources/graphql/overview).
