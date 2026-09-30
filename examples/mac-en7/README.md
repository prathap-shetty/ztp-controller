# Mac + USB Ethernet en7: PoC example

Run the API/database/HTTP server in Docker Desktop and DHCP natively with dnsmasq
on the Mac's en7 adapter. Docker Desktop runs Linux containers inside a VM; its
host networking does not provide the raw physical-interface access used by Kea.
Changing the Linux example's `ens3` to `en7` inside the container will not fix that.
This example therefore contains **no DHCP container**. If you require Kea in Docker,
use the two-interface Linux VM setup instead.

These files have been configuration-checked, but DHCP on your physical USB adapter
and a switch upgrade have not been tested. No interface is changed and no DHCP
service is started automatically by preparing these files.

## 1. Attach the adapter and set its address

Connect en7 to an isolated build VLAN with the new switches' mgmt0 ports. Do not
connect this DHCP scope to an existing management subnet with another DHCP server.
Keep Wi-Fi or another interface for normal access. Disable macOS Internet Sharing
for this adapter to avoid competing DHCP. Ensure no VPN/other interface uses this
same subnet.

```sh
networksetup -listallhardwareports
ifconfig en7
```

In macOS System Settings → Network, select the USB adapter corresponding to en7.
Set IPv4 manually to **10.10.10.1**, subnet mask **255.255.255.0**, with no router.
Preserve its original settings so you can restore them afterward. Alternatively,
for a temporary address (not persistent), on an otherwise unused adapter:

```sh
sudo ifconfig en7 inet 10.10.10.1 netmask 255.255.255.0 up
ipconfig getifaddr en7
```

The startup helper checks that en7 has 10.10.10.1. If the adapter is assigned a
different interface name later, update both the helper and dnsmasq configuration.
The DHCP router option points at the Mac to satisfy POAP; this setup does not
configure internet routing/NAT. Downloads use local IPs.

## 2. Prepare the controller

Start Docker Desktop. From the repository root:

```sh
cp examples/mac-en7/env.example .env.poc.mac
chmod 600 .env.poc.mac
mkdir -p images
```

Edit `.env.poc.mac`, replacing the database and switch admin passwords. It defaults
to local YAML inventory, HTTP on 10.10.10.1:80, and source-version validation disabled
for the PoC. For NetBox instead, set provider `netbox`, its URL/token and platform
slug; NetBox TLS certificate verification is disabled in this PoC.

Copy the licensed Cisco image into `images/`. For a new catalog/bootstrap:

```sh
python3 scripts/prepare_poc.py \
  --image images/nxos64-cs.10.5.4.M.bin \
  --sha256 PASTE_CISCO_SHA256 \
  --model N9K-C93180YC-FX3 \
  --source '10.4(4)M' \
  --target '10.5(4)M' \
  --controller http://10.10.10.1
```

The source field is retained as metadata but not enforced with the PoC bypass.
Exact model and image/hash still match. NX-OS itself may reject an unsupported path.
For an existing catalog, only regenerate the bootstrap instead:

```sh
python3 scripts/release_bootstrap.py --controller http://10.10.10.1 \
  --allow-http --output deploy/bootstrap
```

Fill `inventory/devices.yaml` with the actual serial/PID and image metadata for each
switch, using `inventory/devices.example.yaml` as a starting point. Assign final
management addresses outside the lease pool, for example 10.10.10.150/24 onward.
See [the full lab guide](../../docs/poc-lab-user-guide.md#8-add-your-first-switch).

## 3. Start only the Mac Docker stack

```sh
docker compose --env-file .env.poc.mac -f compose.poc.mac.yaml up --build -d
curl http://10.10.10.1/ready
curl -f http://10.10.10.1/bootstrap/poap.py -o /tmp/poap-mac-test.py
cmp /tmp/poap-mac-test.py deploy/bootstrap/poap.py
```

This standalone file reuses the PoC services via Compose `extends`; do not combine
it with another `-f` file. Do not add a `dhcp` profile. Only HTTP is published on
the build IP; the API developer port is localhost:8001. Allow Docker Desktop
incoming connections through the Mac firewall when needed. If port 80 publication
fails, check Docker Desktop's privileged-port permissions and other listeners.
Do not replace the bind address with 0.0.0.0 without considering other networks.

## 4. Start native DHCP on en7

Install dnsmasq through Homebrew; do not register it as a background brew service:

```sh
brew install dnsmasq
sh examples/mac-en7/start-dhcp.sh
```

Leave that terminal open. The helper checks the interface address and configuration,
then asks sudo to run DHCP in the foreground. The explicit configuration file avoids
loading an unrelated default dnsmasq config. DNS and TFTP are disabled. DHCP offers:

- Pool: 10.10.10.10–10.10.10.149/24 (140 leases).
- Router: 10.10.10.1; DNS: 8.8.8.8.
- Option 66: `http://10.10.10.1`.
- Option 67: `bootstrap/poap.py`.

If UDP 67 is already in use, resolve the competing server rather than killing an
unknown service. The lease file is `/var/db/ztp-poc-en7.leases`. macOS must permit
incoming DHCP traffic; do not disable the entire firewall to troubleshoot this.

## 5. Boot and observe one switch

Boot an unconfigured switch and answer `no` to abort-POAP. In another terminal:

```sh
sudo tcpdump -ni en7 -vv 'udp port 67 or udp port 68'
docker compose --env-file .env.poc.mac -f compose.poc.mac.yaml \
  logs -f nginx ztp-api
```

Look for the DHCP exchange, then an HTTP 200 bootstrap request from the switch's
lease IP. A curl request from the Mac alone is not a physical-network test. Check
console output for image verification, install, reboot and replay. Final verification
is manual (`show version`, running/startup config); CONFIGURING is not proven success.

## 6. Stop and restore

Press Ctrl-C in the DHCP terminal, then:

```sh
docker compose --env-file .env.poc.mac -f compose.poc.mac.yaml down
```

Restore the adapter's prior network settings in System Settings. Retain database
volumes and the lease file while reconciling attempts; do not remove them to force
retries. Do not leave DHCP running when reconnecting en7 to another network.

## References

- [Docker Desktop host-networking limitations](https://docs.docker.com/engine/network/drivers/host/)
- [Docker Desktop networking](https://docs.docker.com/desktop/features/networking/)
- [dnsmasq options](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html)
- [Homebrew dnsmasq](https://formulae.brew.sh/formula/dnsmasq)
