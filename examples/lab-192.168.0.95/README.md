# Reproduce the DHCP and POAP download lab

These examples reproduce the settings used with the switch console on 2026-09-06.
**Proven: DHCP address assignment, HTTP script download, embedded MD5 validation.**
Script execution subsequently failed; configuration replay and complete provisioning
are not qualified. The exact tested hardware PID and running release were not
confirmed. Correct images and a POAP file alone do not guarantee full provisioning.

Use this directory with the full repository, including its pinned Compose images,
Dockerfiles, nginx configuration and bootstrap release tool. The example files contain
no NetBox token, database password, TLS private key or SSH private key.

## Topology and files

| Setting | Lab value |
| --- | --- |
| Linux server management | 192.168.0.95 on ens2 |
| ZTP interface | ens3, 10.10.10.1/24 |
| Switch connection | mgmt0 on the same L2 network as ens3 |
| DHCP pool | 10.10.10.10–10.10.10.50 |
| Option 3 / 6 | router 10.10.10.1 / DNS 8.8.8.8 |
| Option 66 / 67 | http://10.10.10.1 / bootstrap/poap.py |
| Bootstrap API origin | https://10.10.10.1 |
| NetBox | https://192.168.0.210, cisco_nxos platform slug |

`lab.env.example` supplies the Compose settings; `site-http.json` is the tested
DHCP configuration. `site-tftp.json` is an optional alternative, not tested on this
switch. `netplan-ens3.yaml.example` shows only the dedicated interface: merge it into
your host's existing configuration without replacing the management interface or
its default route. The original server already had this persistent address.

The switch required router and DNS options even though the download uses a literal
IP on the same subnet. This stack does not configure upstream routing/NAT or DNS;
external DNS reachability is not established. All bootstrap/API endpoints used here
are local. Adapt routing if your actual workflow needs external services.

## Reconstruct on a fresh Linux amd64 lab host

Install Docker Engine and the Compose plugin. Run the following from the repository
root. These initialization steps are for a fresh deployment; preserve existing
passwords, keys and database volumes when updating a running stack.

```sh
cp examples/lab-192.168.0.95/lab.env.example .env
chmod 600 .env
cp examples/lab-192.168.0.95/site-http.json deploy/kea/site-http.json
cp examples/lab-192.168.0.95/site-tftp.json deploy/kea/site-tftp.json
mkdir -p secrets/ssh-public secrets/ssh-validation deploy/tls deploy/bootstrap
chmod 700 secrets
python3 - <<'PY'
from pathlib import Path
import getpass, secrets
p = Path('.env')
p.write_text(p.read_text().replace('replace-with-a-long-random-url-safe-password', secrets.token_urlsafe(36)))
token = getpass.getpass('Read-only NetBox token: ')
if not token or any(c.isspace() for c in token):
    raise SystemExit('Token must be nonempty without whitespace')
f = Path('secrets/netbox_token')
f.write_text(token)
f.chmod(0o600)
PY
sudo chown 10001:10001 secrets/netbox_token
openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 90 \
  -keyout deploy/tls/server.key -out deploy/tls/server.crt \
  -subj /CN=10.10.10.1 -addext 'subjectAltName=IP:10.10.10.1'
sudo chown 101:101 deploy/tls/server.key
sudo chmod 600 deploy/tls/server.key
python3 scripts/release_bootstrap.py --controller https://10.10.10.1 \
  --ca deploy/tls/server.crt --output deploy/bootstrap
sudo docker compose --profile provisioning config --quiet
sudo docker compose --profile provisioning build
sudo docker compose run --rm --no-deps --entrypoint /usr/sbin/kea-dhcp4 \
  kea-dhcp4 -t /etc/kea/kea-dhcp4.json
sudo docker compose --profile provisioning up -d
```

Replace addresses and interface names consistently before running on another host.
`ZTP_NETBOX_VERIFY_SSL=false` is the explicitly selected self-signed NetBox lab
setting; default application behavior verifies TLS. It does not disable bootstrap
API TLS validation. The generated script embeds the public certificate, not its key.
Renew the certificate and regenerate the script together. Never serve the unreleased
`poap/cisco/poap.py` source directly or reformat a released script: its MD5 header
must reflect its final content. Keep the public bootstrap directory free of secrets.

## Observe the test

At the switch's abort-POAP prompt, choose `no` to continue POAP. No erase/reload is
needed merely to inspect server logs. Keep execution mode at `planning-only` for
this transport test.

```sh
sudo docker compose --profile provisioning ps
sudo docker compose exec -T kea-dhcp4 cat /var/lib/kea/kea-leases4.csv
sudo docker compose logs --tail 100 kea-dhcp4 nginx
sudo timeout 30 tcpdump -ni ens3 -vv 'udp port 67 or udp port 68'
curl --cacert deploy/tls/server.crt https://10.10.10.1/ready
```

A successful client download appears in nginx as HTTP 200 for
`/bootstrap/poap.py` from a switch lease address. A curl request originating from
the server is only a server test. Confirm the switch console reports download and
MD5 validation; neither proves Python execution or configuration success.

Recorded result: switch 9D9D49WT46X received 10.10.10.11/24, downloaded 9598 bytes,
validated MD5 `99852cf5281787811caf1354cdf7e7b0`, and then reported script execution
failure. Gather the script's detailed error/traceback before diagnosing that failure.
See [full evidence](../../docs/dev-server-192.168.0.95.md).

## Optional TFTP selection

Set `ZTP_KEA_CONFIG=./deploy/kea/site-tftp.json` in `.env`, validate Kea as above,
then run `sudo docker compose --profile provisioning --profile tftp up -d`.
TFTP serves only the public bootstrap on UDP 69 and transfer ports 40000–40100.
This is explicit selection, not automatic fallback; HTTP is the demonstrated path.

## Moving from transport test to full provisioning

Resolve the execution failure and qualify Python/CLI support on the exact PID and
NX-OS release first. Enroll the actual serial in NetBox (the console serial differed
from device 27), with staged status, ZTP enabled, primary IPv4 on its own mgmt0,
valid context and an approved matching compatibility profile. Generate validation
SSH keys and enroll the switch's authenticated host key as described in the
[M2a guide](../../docs/m2a-lab-guide.md). Enable configuration-only execution only
for the already-at-target test, then verify replay, SSH validation and saved config.

M2a does not download/install NX-OS images. Correct image filename/checksum metadata
is required for its current intent checks, but image upgrades require later
implementation. Local serial-named config fallback and large-image folder serving
are also not implemented. These examples are a reproducible transport baseline,
not a qualified full POAP deployment for every real Nexus switch.
