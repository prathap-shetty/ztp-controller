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
cp inventory/devices.nxos.example.yaml inventory/devices.yaml
```

Set unique `POSTGRES_PASSWORD`, `ZTP_ADMIN_PASSWORD` and `ZTP_DASHBOARD_TOKEN` in
`.env.lite`. Generate the dashboard token with `openssl rand -hex 32`. Blank token
disables the UI. The switch admin password must be 8–128 characters from
`A-Za-z0-9!@%_+=.-` and meet the device's own password policy. Use a URL-safe database
password because it is embedded in the database connection URL.

## 2. Prepare your image and bootstrap

Copy your licensed NX-OS image to `images/` or set `ZTP_IMAGE_DIR` to a large host
folder. Verify it against Cisco's published digest. If Cisco provides SHA-512,
verify that first, then calculate SHA-256 for this controller:

For **NetBox** with the default `ZTP_CATALOG_SOURCE=auto`, keep the verified image
in the image directory and generate only the bootstrap:

```bash
sha256sum images/nxos64-cs.10.5.4.M.bin
python3 scripts/release_nxos_bootstrap.py --controller http://10.10.10.1 \
  --allow-http --output deploy/bootstrap
```

The API derives the plan from NetBox's target version, filename, checksum and
validated device identity. It reads the size from the non-empty local image;
no local catalog profile is loaded or matched. The device verifies the image
SHA-256 before installation. The local image must be present even for an
already-at-target registration in this implementation.

For **local YAML or InfraHub**, prepare the required catalog and bootstrap:

```bash
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
Local YAML and InfraHub require matching catalog/image metadata, including for
configuration-only NX-OS operation. Empty images are rejected by the preparation tool.

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

With Lite NetBox, `ZTP_CATALOG_SOURCE=auto` uses the context above without a catalog.
The controller's execution mode permits configuration-only or upgrade-and-configure;
context-derived profiles are explicitly unqualified and still require the existing
lab opt-in. This does not establish a Cisco-approved upgrade path. Upgrading from
a different release requires `ZTP_SKIP_SOURCE_VALIDATION=true`, because this context
contains no source-release allowlist. To enforce a source-release list, set
`ZTP_CATALOG_SOURCE=catalog` and supply an explicit compatibility profile instead.
No source validation is silently bypassed.

`ZTP_CATALOG_SOURCE=netbox-context` explicitly selects the same behavior and requires
Lite with NetBox inventory. Non-Lite deployments retain catalog behavior. InfraHub
continues to require a catalog. XR configuration-only behavior is unchanged.

Compose mounts the existing `catalog/` directory by default, so NetBox needs no
placeholder `lite.json`. With local YAML, generate `catalog/lite.json` in that
directory. An existing `ZTP_CATALOG_FILE=/path/to/catalog.json` file mount is also
supported. Recreate containers after upgrading Compose or changing these settings.

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

## Optional: two management VLANs

One controller can serve two management VLANs. Each VLAN needs its own host address,
DHCP subnet and bootstrap, because a bootstrap has one built-in controller URL. The
examples use `ens3.200`/`10.20.0.1` and `ens3.500`/`10.50.0.1`; replace them with
your values. Create both interfaces before starting Kea and Nginx: Kea refuses a
configuration naming a missing interface. Confirm there is no competing DHCP server
or relay on either VLAN.

```bash
python3 scripts/release_nxos_bootstrap.py --controller http://10.20.0.1 --allow-http --output deploy/bootstrap/vlan200
python3 scripts/release_nxos_bootstrap.py --controller http://10.50.0.1 --allow-http --output deploy/bootstrap/vlan500
```

Set these in `.env.lite`:

```bash
ZTP_BIND_ADDRESS=10.20.0.1
ZTP_BIND_ADDRESS_2=10.50.0.1
ZTP_KEA_CONFIG=./examples/multi-vlan/kea.json
```

Add the overlay to every Compose command:

```bash
docker compose --env-file .env.lite -f compose.lite.yaml -f compose.lite.multi-vlan.yaml --profile dhcp up -d --build
curl http://10.20.0.1/ready
curl http://10.50.0.1/ready
```

Boot options are per subnet, so one VLAN can serve NX-OS and the other IOS XR, IOS XE
or Arista EOS. For those, generate with `scripts/release_xr_bootstrap.py`,
`scripts/release_xe_bootstrap.py` or `scripts/release_eos_bootstrap.py` and set
`boot-file-name` to the full URL, for example
`http://10.50.0.1/bootstrap/vlan500/iosxr.py`. Both VLANs share one
inventory, database and dashboard. Do not bind Nginx to `0.0.0.0`: that exposes the
controller and dashboard on every host interface.

## Optional: several platforms on one subnet

`examples/mixed-platform/kea.json` selects each device's script with Kea client
classes matched on DHCP option 60 (vendor class) or 77 (user class). Generate every enabled platform's
bootstrap into `deploy/bootstrap`, then set:

```bash
ZTP_KEA_CONFIG=./examples/mixed-platform/kea.json
```

| Class | Match | Option 67 |
|---|---|---|
| `ztp-eos` | option 60 starts with `Arista;` | `http://10.10.10.1/bootstrap/eos.py` |
| `ztp-iosxe` | option 60 equals `ciscopnp` | `http://10.10.10.1/bootstrap/iosxe.py` |
| `ztp-nxos` | option 77 user class `Cisco-POAP` | `bootstrap/nxos.py` with option 66 `http://10.10.10.1` |

The values come from vEOS 4.30.7M, Catalyst 8000V 17.x and Nexus 9000v POAP
captures. Option 77 uses RFC 3004 format, so the NX-OS test matches the length byte
`0x0a` followed by `Cisco-POAP`. Only the NX-OS class sends option 66. Any other
device that sends `ciscopnp`, such as a different IOS XE model, also receives the
IOS XE script. A client matching no class gets an address but no boot options.
IOS XR is not classified yet; add a class from its captured values. Check a
client's values with:

```bash
sudo tcpdump -i ens3 -nn -vvv -s0 'udp port 67 or udp port 68'
```

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
The dashboard shows “Awaiting manual verification” after staging unless automatic
verification below is enabled.

### Optional: automatic verification

Set `ZTP_LITE_VERIFICATION=true` in `.env.lite` and recreate the stack. The
`ztp-worker` container then checks each new attempt after the device reports its
configuration, retrying every 30 seconds until 30 minutes have passed:

1. Ping the management address from the controller.
2. SSH to it as `admin` with `ZTP_ADMIN_PASSWORD`. The first SSH host key seen is
   pinned for the attempt; a different key later stops verification.
3. Confirm the chassis serial and model, and for NX-OS the target version.
4. Confirm the rendered configuration is in running and startup configuration
   (committed configuration on IOS XR). Password and secret lines are skipped
   because devices store hashes; the successful login checks the password.

All checks passing marks the device **Verified**. The worker runs show commands
only. A failure never marks the attempt failed: the dashboard shows the reason and
missing configuration lines, and **Verify now** in the device row runs the checks
again. Use **Verify now** for attempts made before enabling the setting. The
controller host needs a route to each final management address. The first-use
pin trusts whichever device answers first, so use an isolated network.

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
python3 scripts/release_nxos_bootstrap.py --controller http://10.10.10.1 --allow-http --output deploy/bootstrap
```

The NX-OS bootstrap is served as `nxos.py` and generated by
`scripts/release_nxos_bootstrap.py`; earlier releases served `poap.py` and used
`scripts/release_bootstrap.py`. When upgrading, point Kea `boot-file-name` at `bootstrap/nxos.py` (or your
VLAN path) and recreate Kea. A previously generated `deploy/bootstrap/poap.py` is not
removed or updated automatically; delete it after the switch-over so DHCP cannot
keep serving the stale script.

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
