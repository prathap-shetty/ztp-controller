# ZTP Lite lab user guide

Lab server: **192.168.0.95**. Deployment directory: `/home/cisco/ztp-lite`.
Environment file: `.env.lite`. Compose file: `compose.lite.yaml`.

This deployment uses NetBox at `https://192.168.0.181`, HTTP provisioning,
password-based switch configuration, and manual post-boot verification.
TLS certificate and SSH host-key validation are disabled for this controlled lab.
Do not commit `.env.lite` or copy its secrets into this guide.

## Open the dashboard

From the ZTP network, open **http://10.10.10.1/**.

From your Mac, open an SSH tunnel and leave the terminal connected:

```bash
ssh -L 8080:127.0.0.1:8001 cisco@192.168.0.95
```

Then open **http://localhost:8080/**.

Retrieve the dashboard login token on the server:

```bash
grep '^ZTP_DASHBOARD_TOKEN=' /home/cisco/ztp-lite/.env.lite
```

Enter the value after `=` in the login page. Sessions expire after eight hours.
The dashboard displays serial numbers, versions, provisioning status and events.
With NetBox, devices appear only after successful registration. “Awaiting manual
verification” means configuration was staged, not that post-boot checks passed.

## Lab network and test device

| Item | Value |
| --- | --- |
| Server management | 192.168.0.95 on ens2 |
| ZTP interface | ens3, 10.10.10.1/24 |
| DHCP pool | 10.10.10.10–10.10.10.50 |
| NetBox device | dc1-pod1-ztp-leaf-1, device 31 |
| Chassis serial | 93MK8XNKSEG |
| Chassis model | N9K-C9300V |
| Installed NX-OS | 10.5(4) |
| Target NX-OS | 10.5(4)M |
| Final mgmt0 | 192.168.20.50/24 |
| Final gateway | 192.168.20.1 |

Connect the switch's mgmt0 to the network served by ens3. The final management
network must also provide connectivity to 192.168.20.1 after configuration is
applied. Do not run another DHCP server on the ZTP segment.

## Prepare the catalog

The current controller requires matching image metadata even when the target
version is already installed and no image transfer will occur.

From the Mac repository directory, copy the verified image:

```bash
scp images/nxos64-cs.10.5.4.M.bin cisco@192.168.0.95:/home/cisco/ztp-lite/images/
```

On the server:

```bash
cd /home/cisco/ztp-lite
sha256sum images/nxos64-cs.10.5.4.M.bin
```

Use that SHA-256 below and in NetBox. Verify the original download against Cisco's
published checksum before using it. Create the catalog once; the command refuses
to overwrite an existing catalog:

```bash
python3 scripts/prepare_poc.py \
  --image images/nxos64-cs.10.5.4.M.bin \
  --sha256 YOUR_SHA256 \
  --model N9K-C9300V \
  --source '10.5(4)' \
  --target '10.5(4)M' \
  --catalog catalog/c9300v-test.json
```

The helper retains its historical `prepare_poc.py` name. Set in `.env.lite`:

```dotenv
ZTP_CATALOG_FILE=./catalog/c9300v-test.json
```

## Prepare NetBox device 31

Open https://192.168.0.181/dcim/devices/31/ and configure:

- Serial `93MK8XNKSEG`, status `Staged`.
- Device type model `N9K-C9300V`, manufacturer slug `cisco`.
- Platform slug `cisco_nxos`.
- Primary IPv4 `192.168.20.50/24`, assigned to this device's `mgmt0`.

Select or create the correct device type without renaming a shared generic type.
Add this local config context, substituting the actual SHA-256:

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "target_nxos": "10.5(4)M",
    "image": {
      "filename": "nxos64-cs.10.5.4.M.bin",
      "sha256": "YOUR_SHA256"
    },
    "management": {
      "gateway": "192.168.20.1",
      "vrf": "management",
      "interface": "mgmt0"
    }
  }
}
```

## Start and test

On the server:

```bash
cd /home/cisco/ztp-lite
sudo docker compose --env-file .env.lite -f compose.lite.yaml \
  --profile dhcp up -d --force-recreate ztp-api nginx
curl http://10.10.10.1/ready
sudo docker compose --env-file .env.lite -f compose.lite.yaml \
  logs -f ztp-api nginx kea-dhcp4
```

Readiness should return `{"status":"ready"}`. It checks service and inventory
connectivity; registration validates the device's complete intent and catalog.

If the disposable switch is already in POAP, allow it to retry. Otherwise, erase
its configuration with `write erase` and reload from the console; do not save the
old configuration when prompted. At the abort-POAP prompt choose **no**.

Expected sequence: DHCP, bootstrap download, successful registration, configuration
download and staging. Since 10.5(4) matches 10.5(4)M, installation is skipped.

After completion, inspect from the console:

```text
show version
show hostname
show interface mgmt0
show running-config
show startup-config
```

Confirm the target version, hostname, final IP/default route and saved config.
Retrieve the generated switch admin password on the server when needed:

```bash
grep '^POC_ADMIN_PASSWORD=' /home/cisco/ztp-lite/.env.lite
```

## Repeat after write erase

For a deliberate second run, after the previous installation has finished and
the switch has been erased, archive its previous attempt once:

```bash
cd /home/cisco/ztp-lite
sudo docker compose --env-file .env.lite -f compose.lite.yaml stop ztp-api
sudo docker compose --env-file .env.lite -f compose.lite.yaml \
  run --rm --no-deps ztp-api python -m app.reprovision \
  --serial 93MK8XNKSEG --confirm-erased
sudo docker compose --env-file .env.lite -f compose.lite.yaml up -d ztp-api nginx
```

Do not reset during installation or on every normal retry. The next registration
gets a new attempt ID, avoiding old bootflash checkpoints. No previous attempt
means the reset command returns an error without changing anything.

## Status and troubleshooting

- `403` registration: check chassis serial, model, staged status and ZTP context.
- `409` registration after an erase: reconcile the previous attempt as above.
- `/ready` reports inventory unavailable: check NetBox URL, reachability and token.
- No dashboard device row: inspect registration logs; rejected registrations do not
  create attempt rows.
- SCP permission denied: the `images` directory must be writable by `cisco`.
- Final IP unreachable: verify connectivity to the final 192.168.20.0/24 network.

At the last verified setup, NetBox connectivity, API readiness, dashboard login,
bootstrap delivery and Kea startup passed. Device 31's model/context and image
catalog still required the preparation above; an end-to-end server POAP run was
not yet verified. The old deployment remains under `/home/cisco/ztp-controller`
with its database preserved for rollback.
