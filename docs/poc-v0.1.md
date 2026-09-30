# PoC v0.1: HTTP, local YAML or NetBox, no SSH verification

For a fresh two-interface VM, follow the [step-by-step lab user guide](poc-lab-user-guide.md).

Use `compose.poc.yaml` **by itself**. This is a separate `ztp-poc` Compose project
with its own database volume. The standard stack is unchanged. Stop any old stack
using the same host ports or DHCP interface before starting this one.

The PoC has no TLS certificate setup, no SSH keys, no known_hosts and no validation
worker. It configures a lab admin password. NetBox certificate verification is off
and HTTP NetBox is permitted. HTTP carries configuration/passwords and attempt tokens
in cleartext; use this on the dedicated build network you described.

## 1. Network and image

Use a Linux amd64 Docker host. Connect the switches' mgmt0 ports to a dedicated
build VLAN with no other DHCP server. Give the host's build interface `10.10.10.1/24`.
Edit `deploy/poc/kea.json` if your interface is not `ens3` or your subnet differs.
The example pool is `.10–.149` (140 leases); use final static addresses `.150–.254`
for this example /24, excluding existing equipment. Router/DNS options are included
because the observed POAP client required them. The host is not configured for
upstream routing or DNS, but the bootstrap/API use local literal IPs.

```sh
cp .env.poc.example .env.poc
chmod 600 .env.poc
mkdir -p images
# Copy the Cisco image into images/.
```

Edit `.env.poc`: set `POSTGRES_PASSWORD` and `POC_ADMIN_PASSWORD`. Use an 8–128
character admin password containing only letters, digits or `!@%_+=.-`; avoid `$`,
spaces and quotes. NX-OS must also accept that password under its password policy.
No certificate or key generation is needed.

## 2. One catalog and one bootstrap for the fleet

Run this from the repository root, replacing the SHA-256 with Cisco's published value:

```sh
python3 scripts/prepare_poc.py \
  --image images/nxos64-cs.10.5.4.M.bin \
  --sha256 PASTE_CISCO_SHA256 \
  --model N9K-C93180YC-FX3 \
  --source '10.4(4)M' \
  --target '10.5(4)M' \
  --controller http://10.10.10.1
```

This verifies the image, creates `catalog/poc.json` and releases `deploy/bootstrap/poap.py`
with the HTTP origin and correct embedded MD5. It uses Python's standard library;
no host pip install is needed. Repeat `--model` and `--source` to include additional
**verified model/source combinations using this same image**. Every combination
listed will be allowed, so separate profiles are needed when the combinations differ.
The command marks those explicitly supplied paths approved for this PoC, not
hardware-qualified. It refuses to overwrite an existing catalog.

The catalog is a single JSON array and may contain multiple profiles and images.
For different paths/images, generate a second file using `--catalog catalog/other.json`,
then merge its entries into `catalog/poc.json`, assigning unique profile IDs. Do not
use a wildcard model: a shared target version is not proof that one image or path
supports all models. Exact PID/source release strings are required. Keep each
required binary once in `images/`.

DHCP serves the same bootstrap URL to all devices. The script registers serial/model/
release; the controller selects a profile from the shared catalog. DHCP does not
serve the catalog itself. Image size/SHA-256 and device authorization remain checked.

## 3. Choose inventory

**Local YAML (default):** copy `inventory/devices.example.yaml` to `inventory/devices.yaml`
and list all switches under `devices`. Give each a unique ID, actual serial, exact
model, name, final management IP, staged status, `ztp_enabled: true`, and software
filename/checksum/target matching the catalog. The optional `initial_configuration`
section is not needed by this PoC template. Use the same template for the fleet:
hostname, admin password, SSH enabled/Telnet disabled, mgmt0 address and default route.

**NetBox:** set these values in `.env.poc`:

```dotenv
ZTP_INVENTORY_PROVIDER=netbox
ZTP_NETBOX_URL=https://your-netbox
ZTP_NETBOX_TOKEN=your-token
ZTP_NETBOX_PLATFORM_SLUG=cisco_nxos
```

The devices need the existing staged/serial/PID/primary-mgmt0 and provisioning/ztp
context fields. See `docs/m2a-lab-guide.md` for the context structure. No token file
or NetBox TLS certificate is required in this standalone PoC. Local YAML mode
requires neither a NetBox URL nor a token.

## 4. Start

```sh
docker compose --env-file .env.poc -f compose.poc.yaml --profile dhcp up --build -d
curl http://10.10.10.1/ready
docker compose --env-file .env.poc -f compose.poc.yaml logs -f kea-dhcp4 nginx ztp-api
```

Omit `--profile dhcp` to bring up just the database/API/web server for a smoke test.
Configuration uses `.env.poc`; use the explicit `--env-file` on subsequent commands.
The default bind address is the build interface only. No HTTPS port is published.
The optional API developer port is localhost:8001. Generated image files and
`.env.poc` are excluded from Git and Docker image build context.

## 5. Boot a new switch

Keep console access, boot the unconfigured switch and answer `no` to abort-POAP.
Expected: DHCP → script download/MD5 → inventory authorization → image download/
SHA-256 → native no-reload installation → scheduled configuration → native POAP
reboot/replay. If already at target it skips image installation.

Check `show version`, management reachability, running config and startup config
from the console after reboot. The API stops at `CONFIGURING` after CONFIG_STAGED
with `validation_policy: manual`; that means replay was scheduled, **not** that the
upgrade or saved configuration has been independently verified. There is no SSH
validation job, NetBox activation, or automatic claim of success. Lost callbacks
can leave AUTHORIZED even if the switch progressed; inspect console/checkpoints.

## Limits

The underlying physical install/reboot flow is still unqualified. This Compose
file removes setup requirements, not the need to test one switch per exact model/
source path before a fleet rollout. Python 3 is required; the old 9.3(3) VM with
Python 2.7 is not supported. Interrupted installation/replay checkpoints require
operator reconciliation; failed attempts are not automatically reset. No automatic
rollback, multi-hop upgrade, or concurrent-download batch scheduler is provided.
EPLD is not managed. Existing PoC attempts also conflict if the shared admin password
or desired intent is changed midway through provisioning.


## PoC source-release bypass

The PoC Compose file now defaults `ZTP_SKIP_SOURCE_VALIDATION=true`. Any running
source version may match a profile for the exact model, target and image. Catalog
`source_versions` entries are retained as reference metadata but are not enforced
in this mode. The released bootstrap honors the same manifest policy. Set the
variable to `false` in `.env.poc` to restore source matching. The standard controller
still validates sources and rejects this bypass unless PoC mode is enabled.

Keep one unambiguous profile per model/target/image when bypassing source matching:
multiple entries differing only in their source lists will match simultaneously
and be denied. NX-OS may still reject the installation; this does not implement
intermediate upgrades or prove every source release is compatible.

After updating, rebuild the PoC containers and regenerate the served script:

```sh
python3 scripts/release_bootstrap.py --controller http://10.10.10.1 \
  --allow-http --output deploy/bootstrap
sudo docker compose --env-file .env.poc -f compose.poc.yaml --profile dhcp up --build -d
```

Do not rerun the catalog helper just to release the script: it refuses to overwrite
an existing catalog. Existing attempts may require reconciliation after policy/plan
changes; perform this update before beginning a new attempt.
