# Provision without NetBox: local YAML inventory

Local YAML supports the same planning-only, configuration-only and
upgrade-and-configure paths as NetBox. It replaces the inventory source; it does
not change the image catalog, authorization rules, SSH validation or hardware
qualification requirements. NetBox remains the default. There is no automatic
fallback between sources when an inventory service/file fails.

## Docker Compose setup

Use Docker Compose 2.24+ (`!reset` support). Create a private inventory folder on the
Docker host; it need not be inside the repository:

```sh
mkdir -p /srv/ztp/inventory
cp inventory/devices.example.yaml /srv/ztp/inventory/devices.yaml
```

Edit the example for your exact device. Keep `ztp_enabled: false` until the serial,
model, management address, target release and real image SHA-256 are correct. The
example's all-zero digest is a placeholder, not an approved image checksum. Set
`ztp_enabled: true` to authorize an otherwise eligible staged device. Each device
needs a unique stable string ID and unique serial. Serial and model normalize to
uppercase; the platform is `nxos` and vendor `cisco`. Quote IDs and digests.

The `management` section is the local equivalent of NetBox's primary IP assignment
to this device's `mgmt0`; no separate NetBox IP/interface record is required. The
interface must be `mgmt0`, VRF `management`, and gateway must be a different usable
address in that subnet. Use an address outside the DHCP pool. The existing exact
serial/model/status checks still apply. An unknown device is denied.

Set in `.env` (retain database password and other deployment settings):

```dotenv
ZTP_INVENTORY_PROVIDER=local-yaml
ZTP_LOCAL_INVENTORY_DIR=/srv/ztp/inventory
# Start with planning-only for inventory checks.
ZTP_EXECUTION_MODE=planning-only
```

Then run from the project root:

```sh
docker compose -f compose.yaml -f compose.local.yaml config --quiet
docker compose -f compose.yaml -f compose.local.yaml --profile provisioning up --build -d
```

The override sets local mode for the API, worker and migration service and removes
their NetBox secret mounts. **No NetBox URL, token, token file or running NetBox
instance is needed.** Setting only the provider variable with the base Compose file
still leaves the base secret mount, so use the override as shown. Include both
`-f` arguments in subsequent Compose commands. All services mount the inventory
directory read-only at `/run/ztp-inventory`. This directory mount supports atomic
replacement of `devices.yaml`; ensure UID 10001 can traverse/read it. Do not store
inventory under the public bootstrap directory. NetBox TLS variables are ignored
in local mode.

The committed `inventory/devices.yaml` is intentionally empty and authorizes nobody.
Use an external folder for real site inventory to keep it out of Git. The example
file is `inventory/devices.example.yaml`; it contains the complete YAML schema.
The image compatibility catalog is still a separate JSON file. Software metadata
in YAML must match that catalog. Images continue to use `ZTP_IMAGE_DIR`.

## Application without Compose

Set `ZTP_INVENTORY_PROVIDER=local-yaml`,
`ZTP_LOCAL_INVENTORY_PATH=/absolute/path/devices.yaml`, and `ZTP_DATABASE_URL`.
Other execution-mode settings are unchanged. The application does not load `.env`
automatically outside Compose. Install dependencies from the updated lock file.

## Edits, revocation and failure behavior

The provider rereads the file for each lookup, including worker and artifact
policy checks. Use an atomic rename to publish a fully written replacement file.
Set `ztp_enabled: false`, remove the entry, or change its status from `staged` to
revoke execution through the normal authorization checks. Previously issued tokens
do not bypass inventory rechecks. A download already in progress may complete;
new requests and the pre-install callback recheck authorization.

Changing desired configuration or software during an active attempt produces an
intent/attempt conflict and needs reconciliation. Switching between NetBox and
YAML is not automatic migration: IDs and normalized intent must match existing
history or the attempt is rejected. No inventory source is written back to or
marked active; success remains recorded in PostgreSQL as VALIDATED.

`/ready` validates readability and the full YAML schema. Empty inventory is valid
but denies registration. Missing/unreadable files produce inventory-unavailable;
invalid YAML, duplicate keys/IDs/serials, aliases, unknown fields, unsafe tags,
invalid management/image data and files larger than 1 MiB fail closed. Error
responses do not include raw file contents.

For execution, configure keys/TLS and the image catalog using the
[M2a guide](m2a-lab-guide.md) or [M2b upgrade guide](m2b-upgrade-lab-guide.md).
Local inventory does not make the unqualified physical upgrade path qualified.
