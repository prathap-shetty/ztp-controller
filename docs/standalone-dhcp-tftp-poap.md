# Standalone NX-OS POAP with Kea DHCP and TFTP

This basic lab setup uses native Linux services and your existing standalone Cisco
POAP Python script. It does not require Docker, HTTP, the controller API, NetBox or
InfraHub. Commands assume Ubuntu 24.04/Debian and a dedicated provisioning network
with no competing DHCP server.

| Setting | Example |
|---|---|
| Provisioning interface | `ens3` |
| Linux provisioning address | `10.10.10.1/24` |
| DHCP pool | `10.10.10.10–10.10.10.50` |
| TFTP root | `/srv/tftp` |
| Bootstrap filename | `poap.py` |

## 1. Install packages and assign an address

```bash
sudo apt update
sudo apt install -y kea-dhcp4-server tftpd-hpa tftp-hpa tcpdump
```

Stop any existing DHCP service/container on this provisioning network before
starting Kea. Keep the other server interface for SSH access.

For a temporary test, if the address is not already configured:

```bash
sudo ip link set ens3 up
sudo ip address add 10.10.10.1/24 dev ens3
ip -4 address show ens3
```

This address does not persist across reboot; use your distribution's network
configuration for a permanent setup.

## 2. Prepare the files

Use the device's chassis serial number, not a supervisor/module serial. Replace
`SERIAL_NUMBER` below with the actual chassis serial, preserving its case.

```text
/srv/tftp/
├── poap.py
├── nxos64-cs.10.5.4.M.bin
├── nxos64-cs.10.5.4.M.bin.md5
├── SERIAL_NUMBER.conf
└── SERIAL_NUMBER.md5
```

```bash
sudo install -d -m 755 /srv/tftp
sudo cp /path/to/your/poap.py /srv/tftp/poap.py
# Copy your chosen image and serial-named configuration into this directory too.
sudo chmod 644 /srv/tftp/poap.py
```

Configure your Cisco script to use TFTP server `10.10.10.1`, discover the chassis
serial, and download `<serial>.conf` and `<serial>.md5`. Configure its image filename
and image checksum filename as well. The image shown is only a filename example;
use an image and upgrade path supported by your hardware.

**The script must support this naming convention.** DHCP does not substitute the
serial or interpret checksum files. The controller's API-dependent bootstrap
cannot be used unchanged here. The standalone script itself controls installation,
configuration replay, version checks and reboot behavior.

### Generate checksum files

If your script expects sidecars containing only the hexadecimal MD5 digest:

```bash
cd /srv/tftp
md5sum nxos64-cs.10.5.4.M.bin | awk '{print $1}' \
  | sudo tee nxos64-cs.10.5.4.M.bin.md5 >/dev/null
md5sum SERIAL_NUMBER.conf | awk '{print $1}' \
  | sudo tee SERIAL_NUMBER.md5 >/dev/null
sudo chmod 644 nxos64-cs.10.5.4.M.bin nxos64-cs.10.5.4.M.bin.md5 \
  SERIAL_NUMBER.conf SERIAL_NUMBER.md5
```

Regenerate the config MD5 after every configuration edit. Verify the image against
Cisco's published checksum before using it; generating a local digest alone does
not establish the downloaded image's authenticity. MD5 here is for compatibility
with the standalone script and is separate from the controller's SHA-256 checks.

Some scripts expect a different sidecar name or `md5sum` output including the
filename. Check your script's checksum parser before choosing a format. If you edit
`poap.py`, regenerate its embedded checksum using that script's documented method;
the image/config sidecars do not replace the bootstrap's embedded checksum.

## 3. Configure TFTP

Edit `/etc/default/tftpd-hpa`:

```bash
TFTP_USERNAME="tftp"
TFTP_DIRECTORY="/srv/tftp"
TFTP_ADDRESS="10.10.10.1:69"
TFTP_OPTIONS="--secure --verbose --port-range 30000:30100"
```

Paths requested by the switch are relative to `/srv/tftp`: request `poap.py`, not
`/srv/tftp/poap.py`. Files must be readable; no upload permission is needed.

## 4. Configure Kea DHCP

Back up the existing file before editing:

```bash
sudo cp /etc/kea/kea-dhcp4.conf /etc/kea/kea-dhcp4.conf.backup
sudo nano /etc/kea/kea-dhcp4.conf
```

Use:

```json
{
  "Dhcp4": {
    "interfaces-config": {"interfaces": ["ens3"]},
    "lease-database": {
      "type": "memfile",
      "persist": true,
      "name": "/var/lib/kea/kea-leases4.csv"
    },
    "valid-lifetime": 3600,
    "renew-timer": 900,
    "rebind-timer": 1800,
    "subnet4": [{
      "id": 1,
      "subnet": "10.10.10.0/24",
      "interface": "ens3",
      "pools": [{"pool": "10.10.10.10 - 10.10.10.50"}],
      "next-server": "10.10.10.1",
      "boot-file-name": "poap.py",
      "option-data": [
        {"name": "routers", "data": "10.10.10.1", "always-send": true},
        {"name": "domain-name-servers", "data": "8.8.8.8", "always-send": true},
        {"name": "tftp-server-name", "data": "10.10.10.1", "always-send": true},
        {"name": "boot-file-name", "data": "poap.py", "always-send": true}
      ]
    }],
    "loggers": [{
      "name": "kea-dhcp4",
      "output_options": [{"output": "stdout"}],
      "severity": "INFO"
    }]
  }
}
```

Options 66/67 identify the TFTP server and script for conventional POAP. Options
3/6 supply router and DNS values required by the lab switch. Every switch downloads
`poap.py`; your script selects its serial-named configuration afterwards.

Advertising `10.10.10.1` as a gateway does not enable Linux routing. Same-subnet
TFTP does not need routing. `8.8.8.8` requires a working route if actually used;
use IP addresses in the script for an isolated test without DNS.

## 5. Validate and start services

```bash
sudo kea-dhcp4 -t /etc/kea/kea-dhcp4.conf
sudo systemctl enable kea-dhcp4-server tftpd-hpa
sudo systemctl restart kea-dhcp4-server tftpd-hpa
sudo systemctl status kea-dhcp4-server tftpd-hpa --no-pager
```

If UFW is active, allow traffic on the dedicated interface:

```bash
sudo ufw allow in on ens3 to any port 67 proto udp
sudo ufw allow in on ens3 from 10.10.10.0/24 to any port 69 proto udp
sudo ufw allow in on ens3 from 10.10.10.0/24 to any port 30000:30100 proto udp
```

The additional range is for TFTP transfer ports, matching the server configuration.

## 6. Test a TFTP download

```bash
cd /tmp
tftp 10.10.10.1
```

At its prompt:

```text
binary
get poap.py
get SERIAL_NUMBER.conf
get SERIAL_NUMBER.md5
quit
```

Compare the downloaded script with the served copy:

```bash
sha256sum /tmp/poap.py /srv/tftp/poap.py
```

Matching hashes confirm the local transfer. Repeat from another Linux machine on
the provisioning network to also check the network/firewall path.

## 7. Boot the switch and watch

Connect `mgmt0` to the provisioning network. Boot a new/erased switch into POAP and
answer **no** when asked whether to abort POAP.

```bash
sudo journalctl -u kea-dhcp4-server -u tftpd-hpa -f
```

For packet-level troubleshooting, in another terminal:

```bash
sudo tcpdump -ni ens3 'udp port 67 or udp port 68 or udp port 69 or udp portrange 30000-30100'
```

Expected sequence:

1. Switch receives an address in the DHCP pool.
2. Switch downloads `poap.py` over TFTP and validates its embedded checksum.
3. Script discovers the chassis serial and fetches its configuration and MD5.
4. Script downloads/verifies the image and performs its configured actions.

Actual ordering and whether installation is skipped depend on your script. If the
bootstrap downloads and validates but execution fails, inspect the switch's POAP
logs, Python compatibility, requested filenames and checksum format. A successful
script transfer alone does not prove successful installation or config replay.

## References

- [Cisco POAP process](https://developer.cisco.com/docs/nx-os/poap-process/)
- [Kea DHCPv4 configuration](https://kea.readthedocs.io/en/kea-3.0.0/arm/dhcp4-srv.html)
- [Ubuntu TFTP setup](https://help.ubuntu.com/community/TFTP)
