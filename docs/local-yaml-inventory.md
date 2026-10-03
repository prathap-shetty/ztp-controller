# Local YAML inventory

Copy `inventory/devices.example.yaml` to `inventory/devices.yaml`, then configure
real device IDs, serials, model names, final management addresses and matching
image metadata. Set `ztp_enabled: true` for eligible staged devices only.

In `.env.lite`, set `ZTP_INVENTORY_PROVIDER=local-yaml` and
`ZTP_LOCAL_INVENTORY_DIR=./inventory`. The directory is mounted read-only and
records are re-read on access so revocations take effect. Device IDs and chassis
serials must be unique. Serial/model values normalize to uppercase.

Use vendor `cisco`, platform `nxos`, interface `mgmt0` and VRF `management`.
The gateway must be another usable address in the same subnet. Image filename,
SHA-256 and target version must match a unique catalog profile. There is no
automatic fallback to NetBox or InfraHub. Invalid YAML or unsupported fields fail
closed. Keep this local file out of Git.

See the [user guide](ztp-lite-user-guide.md) for provisioning and restart commands.
