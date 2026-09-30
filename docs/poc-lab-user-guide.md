# PoC v0.1 lab user guide

This guide sets up a dedicated Ubuntu 24.04 amd64 VM with two network interfaces
for first-boot provisioning of new NX-OS switches. The PoC uses HTTP and a switch
admin password; no TLS certificates, SSH keys or known_hosts enrollment are needed.
Choose local YAML or NetBox for inventory. Final validation is performed manually
from the console.

Start with one switch. DHCP/script delivery has been demonstrated, but physical
image installation, reboot and replay are not yet qualified. Confirm the exact
hardware/source/target upgrade path with Cisco before allowing it in the catalog.

## 1. Connect the lab

| Interface | Purpose | Address |
| --- | --- | --- |
| ens2 | Existing VM management/internet | Keep existing settings |
| ens3 | Dedicated ZTP VLAN | 10.10.10.1/24 |

Connect switch mgmt0 ports to the same isolated VLAN as ens3. There must be no
other DHCP server on that VLAN. Keep switch console access. HTTP sends passwords
and attempt tokens in cleartext, so keep this PoC on the dedicated build network.
Stop any previous stack using the same HTTP ports or DHCP interface.

## 2. Copy the project to the VM

Use a checkout containing `compose.poc.yaml`. If the PoC commit has been pushed,
clone or pull the repository on the VM:

```sh
git clone https://github.com/prathap-shetty/ztp-controller.git
cd ztp-controller
# For an existing checkout instead:
# git pull
```

If the commit is still only on your Mac, run this on the Mac, replacing the VM
user/address. This copies source without private runtime files:

```sh
cd /Users/prathapshetty/Documents/dev_workspace/ztp-controller
rsync -av \
  --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='.env' --exclude='.env.poc' --exclude='secrets/' \
  --exclude='images/' --exclude='deploy/tls/' --exclude='deploy/bootstrap/' \
  ./ YOUR_VM_USER@YOUR_VM_IP:~/ztp-controller/
```

All remaining commands run on the VM from `~/ztp-controller`, unless noted.

## 3. Install Docker

```sh
sudo apt-get update
sudo apt-get install -y docker.io docker-compose-v2 python3
sudo systemctl enable --now docker
sudo docker compose version
cd ~/ztp-controller
```

## 4. Give ens3 its static address

```sh
ip -br address
sudo ls /etc/netplan
```

Preserve the management interface and its default route. If ens3 is not already
configured elsewhere, create `/etc/netplan/60-ztp.yaml`:

```yaml
network:
  version: 2
  ethernets:
    ens3:
      dhcp4: false
      addresses:
        - 10.10.10.1/24
```

```sh
sudo chmod 600 /etc/netplan/60-ztp.yaml
sudo netplan try
ip -br address show ens3
```

Confirm ens3 shows `10.10.10.1/24`. The VM's default gateway stays on ens2.

## 5. Configure the PoC environment

```sh
cp .env.poc.example .env.poc
chmod 600 .env.poc
mkdir -p images
```

Edit `.env.poc`:

```dotenv
POSTGRES_PASSWORD=ReplaceWithRandomDatabasePassword
POC_ADMIN_PASSWORD=ReplaceWithStrongLabPassword123
ZTP_INVENTORY_PROVIDER=local-yaml
ZTP_LOCAL_INVENTORY_DIR=./inventory
ZTP_CATALOG_FILE=./catalog/poc.json
ZTP_IMAGE_DIR=./images
ZTP_BIND_ADDRESS=10.10.10.1
ZTP_HTTP_PORT=80
ZTP_API_PORT=8001
ZTP_BOOTSTRAP_DIR=./deploy/bootstrap
ZTP_KEA_CONFIG=./deploy/poc/kea.json
```

Replace both passwords. `POC_ADMIN_PASSWORD` becomes the switch admin password;
use 8–128 characters, preferably mixed-case letters and numbers, that satisfy the
switch's password policy. The accepted punctuation is `!@%_+=.-`; avoid spaces,
quotes and `$`. For the database, `openssl rand -hex 24` generates a URL-safe value.
Do not change the database password after initialization without updating the database.

## 6. Download and copy the image

Obtain the licensed image and its published SHA-256 from Cisco. For the FX3 example,
the target file is `nxos64-cs.10.5.4.M.bin`. From the Mac, for example:

```sh
scp /path/to/nxos64-cs.10.5.4.M.bin \
  YOUR_VM_USER@YOUR_VM_IP:~/ztp-controller/images/
```

The image remains in the host folder and is mounted read-only; it is not baked into
the Docker image. Ensure container UID 10001 can read the file.

## 7. Generate the catalog and released bootstrap

On the VM, replace the checksum and use the exact source release reported by the
switch. This example is for the previously discussed FX3 path:

```sh
python3 scripts/prepare_poc.py \
  --image images/nxos64-cs.10.5.4.M.bin \
  --sha256 PASTE_CISCO_SHA256 \
  --model N9K-C93180YC-FX3 \
  --source '10.4(4)M' \
  --target '10.5(4)M' \
  --controller http://10.10.10.1
```

This verifies the actual image and creates:

```text
catalog/poc.json
deploy/bootstrap/poap.py
deploy/bootstrap/poap.py.sha256
```

The helper refuses to overwrite an existing catalog. It approves the explicitly
specified model/source combinations for the PoC; only list paths you have checked.
Do not reformat/edit the released script: regenerate it to keep its MD5 correct.

For a mixed fleet, keep one catalog JSON array with multiple profiles and one YAML
file with all devices. Repeat `--model` and `--source` only when **every combination**
is valid for that image. Different combinations/images need separate entries in
the same catalog with unique profile IDs. See [PoC reference](poc-v0.1.md#2-one-catalog-and-one-bootstrap-for-the-fleet).
DHCP points all devices to the same script; the API selects the matching profile.

## 8. Add your first switch

```sh
cp inventory/devices.example.yaml inventory/devices.yaml
```

Replace the file contents with your device details:

```yaml
schema_version: 1
devices:
  - device:
      id: "leaf-01"
      name: leaf-01
      serial_number: REPLACE_WITH_ACTUAL_CHASSIS_SERIAL
      vendor: cisco
      platform: nxos
      model: N9K-C93180YC-FX3
      status: staged
    ztp_enabled: true
    management:
      address: 10.10.10.150/24
      gateway: 10.10.10.1
      vrf: management
      interface: mgmt0
    software:
      target_version: "10.5(4)M"
      image_name: nxos64-cs.10.5.4.M.bin
      image_checksum: "PASTE_THE_SAME_CISCO_SHA256"
```

Use the actual serial and exact PID, not a shortened model family. The software
fields must match the catalog. Add entries for other devices with unique IDs,
serials, hostnames and final IPs. The initial_configuration section is optional for
this PoC. Do not commit real site inventory/passwords inadvertently.

### NetBox alternative

Instead of local inventory, set these in `.env.poc`:

```dotenv
ZTP_INVENTORY_PROVIDER=netbox
ZTP_NETBOX_URL=https://YOUR_NETBOX
ZTP_NETBOX_TOKEN=YOUR_TOKEN
ZTP_NETBOX_PLATFORM_SLUG=cisco_nxos
```

The device must be staged with its actual serial/PID, primary IPv4 assigned to its
own mgmt0, and the provisioning/ztp config context. See the
[NetBox context setup](m2a-lab-guide.md#1-inventory-and-profile). The PoC ignores
NetBox certificate verification; no token file is needed. This changes inventory
source only; keep using `compose.poc.yaml` by itself.

## 9. Check DHCP configuration

`deploy/poc/kea.json` already specifies:

| Setting | Value |
| --- | --- |
| Interface/subnet | ens3 / 10.10.10.0/24 |
| Temporary lease pool | 10.10.10.10–10.10.10.149 |
| Router / DNS | 10.10.10.1 / 8.8.8.8 |
| Script server / filename | http://10.10.10.1 / bootstrap/poap.py |

Final static addresses must be outside the pool; `.150–.254` are available in this
example if not already used. Router/DNS options were required by the observed POAP
client. The stack does not configure upstream routing or DNS service; local image/
script/API downloads use literal addresses and do not depend on external DNS.

## 10. Build and test HTTP before enabling DHCP

```sh
sudo docker compose --env-file .env.poc -f compose.poc.yaml up --build -d
curl http://10.10.10.1/ready
curl -f http://10.10.10.1/bootstrap/poap.py -o /tmp/poap-test.py
cmp /tmp/poap-test.py deploy/bootstrap/poap.py
```

Readiness should return `{"status":"ready"}`; no output from `cmp` means the
bootstrap bytes match. Use `docker compose --env-file .env.poc -f compose.poc.yaml ps`
to inspect containers. Readiness does not prove hardware compatibility.

## 11. Validate and enable DHCP

```sh
sudo docker compose --env-file .env.poc -f compose.poc.yaml --profile dhcp \
  run --rm --no-deps --entrypoint /usr/sbin/kea-dhcp4 \
  kea-dhcp4 -t /etc/kea/kea-dhcp4.json

sudo docker compose --env-file .env.poc -f compose.poc.yaml --profile dhcp up -d
```

If a host firewall is enabled, allow DHCP UDP 67/68 and HTTP TCP 80 on the dedicated
ZTP network. This configuration uses HTTP; the PoC does not start TFTP or HTTPS.

## 12. Boot and observe the new switch

Connect console and mgmt0, then power on the unconfigured switch. At the abort-POAP
prompt, enter `no` to continue POAP. Do not erase a configured switch as part of
this new-device procedure.

```sh
sudo docker compose --env-file .env.poc -f compose.poc.yaml \
  logs -f kea-dhcp4 nginx ztp-api
```

Expected sequence: DHCP → script download/MD5 → inventory match → image download/
SHA-256 → installation → native POAP reboot → configuration replay. If already at
the target version, the script skips image installation.

## 13. Confirm the result manually

From the console after reboot:

```text
show version
show interface mgmt0
show running-config
show startup-config
```

Confirm target software, hostname, final management IP and saved configuration.
The controller's `CONFIGURING` state and `validation_policy: manual` mean replay
was scheduled, not that post-reboot success was independently verified. There is
no SSH validation worker, no NetBox activation and no automatic final success claim.

If a stage fails, retain console output, API logs and bootflash checkpoint files.
Do not delete checkpoints/database history to force a retry without reconciling
actual device state. Test one device per exact model/source combination before
larger batches; the PoC does not coordinate concurrent upgrades for an 80-device fleet.

## Stop the lab

Stop DHCP first when finished:

```sh
sudo docker compose --env-file .env.poc -f compose.poc.yaml --profile dhcp stop kea-dhcp4
sudo docker compose --env-file .env.poc -f compose.poc.yaml --profile dhcp down
```

Do not add `--volumes` unless intentionally discarding lab database and lease history.

## Verification record

The implementation passed 97 automated tests. A local Docker smoke test started
PostgreSQL, migrations, API and HTTP nginx without certificates/SSH keys; readiness
and bootstrap byte comparison passed. That smoke test did not enable DHCP or run
an actual switch upgrade. Full physical install/replay qualification remains pending.
