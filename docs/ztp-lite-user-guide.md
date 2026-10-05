# ZTP Lite user guide

## 1. Prepare a Linux host

Use Docker Engine with Docker Compose v2, Python 3, a management connection and a
separate NIC (example `ens3`) connected to the provisioning network. Configure
that NIC with `10.10.10.1/24`, without replacing the host's existing default route.
Ensure no competing DHCP server serves this segment. Adjust all example addresses
to your actual network before starting DHCP.

```bash
cp .env.lite.example .env.lite
chmod 600 .env.lite
mkdir -p images inventory
cp inventory/devices.example.yaml inventory/devices.yaml
```

Set unique `POSTGRES_PASSWORD`, `ZTP_ADMIN_PASSWORD` and `ZTP_DASHBOARD_TOKEN` in
`.env.lite`. Generate the dashboard token with `openssl rand -hex 32`. Blank token
disables the UI. The switch admin password must be 8–128 characters from
`A-Za-z0-9!@%_+=.-` and meet the device's own password policy. Use a URL-safe database
password because it is embedded in the database connection URL.

## 2. Prepare your image and catalog

Copy your licensed NX-OS image to `images/` or set `ZTP_IMAGE_DIR` to a large host
folder. Verify it against Cisco's published digest. If Cisco provides SHA-512,
verify that first, then calculate SHA-256 for this controller:

```bash
sha256sum images/nxos64-cs.10.5.4.M.bin
python3 scripts/prepare_lite.py \
  --image images/nxos64-cs.10.5.4.M.bin \
  --sha256 YOUR_VERIFIED_SHA256 \
  --model N9K-C93180YC-FX3 \
  --source '10.4(4)M' \
  --target '10.5(4)M' \
  --controller http://10.10.10.1
```

This creates `catalog/lite.json` and the released POAP script under
`deploy/bootstrap/`. The helper refuses to overwrite an existing catalog. Choose
the exact chassis PID reported by `show inventory`; repeat `--model` only for
models qualified for the same image/path. Edit or create separate catalog profiles
when image families or approved paths differ. The helper marks the path approved
for this controlled test; it does not establish vendor or hardware qualification.

Image filenames are preserved on bootflash. Size and SHA-256 are verified. A file
with the same name but different contents stops provisioning without overwrite.
Even configuration-only operation requires matching catalog/image metadata.

## 3. Choose the inventory source

For local YAML, populate `inventory/devices.yaml` from the committed example.
Use real serials, correct model, final management IP and matching catalog metadata.
Set `ztp_enabled: true` only after preparing an eligible staged device. Keep final
IPs outside the DHCP pool.

For NetBox, set `ZTP_INVENTORY_PROVIDER=netbox`, `ZTP_NETBOX_URL`,
`ZTP_NETBOX_TOKEN` and `ZTP_NETBOX_PLATFORM_SLUG`. The device needs a matching
chassis serial/model, Cisco manufacturer slug `cisco`, staged status and a primary
IPv4 address assigned to its own `mgmt0`. Add local config context:

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "target_nxos": "10.5(4)M",
    "image": {"filename": "nxos64-cs.10.5.4.M.bin", "sha256": "YOUR_VERIFIED_SHA256"},
    "management": {"gateway": "10.10.10.1", "vrf": "management", "interface": "mgmt0"}
  }
}
```

For InfraHub, follow the [schema and mapping guide](infrahub-inventory.md).
No automatic fallback occurs when the selected inventory source is unavailable.

## 4. Start and check

Review `deploy/lite/kea.json`: interface `ens3`, subnet `10.10.10.0/24`, pool
`10.10.10.10–149`, router/script host `10.10.10.1`, DNS `8.8.8.8`. Replace the DNS
example with a reachable resolver where appropriate. Both DHCP router and DNS
options are included because NX-OS POAP requires them.

```bash
docker compose --env-file .env.lite -f compose.lite.yaml --profile dhcp up -d --build
curl http://10.10.10.1/ready
docker compose --env-file .env.lite -f compose.lite.yaml logs -f ztp-api nginx kea-dhcp4
```

Readiness confirms database/inventory access, not per-device eligibility. Database
migrations run before the API. Commands may require `sudo` on your host.

Open `http://10.10.10.1/` and enter `ZTP_DASHBOARD_TOKEN`. Alternatively, tunnel the
loopback API port from your workstation:

```bash
ssh -L 8080:127.0.0.1:8001 USER@CONTROLLER_HOST
```

Then open `http://localhost:8080/`. Cookie sessions expire after eight hours;
rotating the environment token and recreating the API invalidates them. Login is
rate-limited. Neither token login nor the isolated-network assumption encrypts HTTP.

## 5. Provision and verify

Connect a new or deliberately erased switch's mgmt0 to the ZTP network. Allow
POAP to continue (`no` at the abort prompt). Expected flow: DHCP, script download,
registration, optional image download/install, configuration staging and native
POAP reboot/replay. Never erase a working switch without planning the disruption.

The default `ZTP_ALLOW_NEWER_VERSION=true` skips installation for an equal/newer
supported numeric release. `10.5(4)` equals `10.5(4)M`. Unknown suffixes are not
ordered. `ZTP_SKIP_SOURCE_VALIDATION=true` bypasses catalog source-release matching;
NX-OS may still reject an unsupported install. Neither option qualifies an upgrade
path. Set the options to false when exact catalog policy is required.

After boot, inspect `show version`, `show hostname`, `show interface mgmt0`,
`show ip route vrf management` and `show startup-config`. Test gateway connectivity.
The dashboard deliberately shows “Awaiting manual verification” after staging.

## 6. Reprovision after an erase

Once the previous installation is finished and the device has been deliberately
erased, reset its controller attempt once from the dashboard:

1. Sign in and find the device in the provisioning attempts table.
2. Click **Reset for reprovisioning** beside **View** in its row.
3. Click **OK** to confirm. The UI uses the device's recorded serial automatically.
4. Let the erased switch retry POAP. The new attempt reads the current inventory
   and renders the updated template. With the default version policy, an equal or
   newer supported software release skips installation and applies configuration.

This resets the controller record only; it does not erase or reload the switch.
The API can remain running. Archived attempts retain their history and cannot be
reset again. A stale browser cannot reset a replacement attempt.

Alternatively, use the CLI:

```bash
docker compose --env-file .env.lite -f compose.lite.yaml stop ztp-api
docker compose --env-file .env.lite -f compose.lite.yaml run --rm --no-deps ztp-api \
  python -m app.reprovision --serial REAL_SERIAL --confirm-erased
docker compose --env-file .env.lite -f compose.lite.yaml up -d ztp-api nginx
```

This archives history and revokes old tokens. The new attempt ignores old switch
checkpoints through its new ID. Do not reset during installation or normal retries.

## Updates and diagnostics

Back up the database and environment first. Rebuild the API and regenerate the
served bootstrap after script updates:

```bash
docker compose --env-file .env.lite -f compose.lite.yaml up -d --build ztp-api nginx
python3 scripts/release_bootstrap.py --controller http://10.10.10.1 --allow-http --output deploy/bootstrap
```

Recent failures shows the latest 100 rejected registrations, with storage capped
at 1,000 records. It includes missing platforms, malformed checksums, inventory
mismatches and reconciliation errors. Old failures are not reconstructed.
Malformed request bodies and rate-limit rejections are not recorded there.
Switch-side failures still require console logs. No credentials or raw config
contexts are displayed.

## Inventory branches

Both providers use `main` when the branch setting is omitted or blank:

```dotenv
ZTP_NETBOX_BRANCH=main
ZTP_INFRAHUB_BRANCH=main
```

For NetBox, set `ZTP_NETBOX_BRANCH` to the branch's eight-character **schema ID**
from its detail page, for example `td5smq0f`, not its display name or numeric ID.
Non-main branches require the NetBox Branching plugin. Main omits the branch header
and works without the plugin. All inventory requests use the selected branch.
See the [NetBox branching API](https://netboxlabs.com/docs/branching/rest-api/).
For InfraHub, use the branch name, for example `dc-build`.
Recreate the API container after changing these environment variables. Existing
attempts retain their saved intent; use reprovisioning for a fresh inventory read.
