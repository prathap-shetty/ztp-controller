# M2a configuration-only lab guide

This implements configuration-only POAP for an already-at-target switch. It does
not download/install images or mark NetBox active. Software tests use fixtures;
physical C93180 replay is **not qualified yet**.

## Editable example

| Input | Example to replace |
| --- | --- |
| Model | `N9K-C93180YC-FX3` (use your exact C93180 PID) |
| Factory/running and target release | `10.5(2)F` (use the exact CLI release string) |
| Linux host | amd64 host, `eth1`, `192.0.2.2/24` |
| ZTP subnet and gateway | `192.0.2.0/24`, `192.0.2.1` |
| DHCP temporary pool | `192.0.2.100–192.0.2.199` |
| Final switch management | `192.0.2.10/24`, `mgmt0`, VRF `management` |
| DNS / NTP | `192.0.2.1` (external services, replace) |
| HTTP / HTTPS | host ports 80 / 443 |

All `192.0.2.x` addresses are documentation examples. Use a dedicated ZTP VLAN;
keep final static management IPs outside the DHCP pool. The worker's source IP as
seen by the switch (often the Linux host after Docker NAT) must be allowed by the
rendered SSH ACL. No router, DNS server or NTP service is created by this stack.

## 1. Inventory and profile

Pre-create the switch in NetBox with its exact uppercase serial, Cisco manufacturer,
NX-OS platform, staged status and primary IPv4 assigned to its own `mgmt0`.
Use the Config Context from the README plus:

```yaml
ztp:
  initial_configuration:
    dns_servers: [192.0.2.1]
    ntp_servers: [192.0.2.1]
    ssh_sources: [192.0.2.0/24]
```

Merge this into the existing `ztp` mapping; preserve `target_nxos`, `image` and
`management`. `provisioning.ztp_enabled` must be the boolean `true`.

Copy `catalog/c93180-nxos105.example.json` to a site-owned catalog. Replace its
PID, source/target release, image filename, size and checksum with approved values
matching NetBox. The zero digest and size 1 are placeholders, not real Cisco image
metadata. M2a uses those fields for planning consistency; it never transfers an
image. Keep `hardware_qualified: false` while qualifying the lab. Select the explicit
`scheduled-config-exit` replay method and use `ZTP_ALLOW_UNQUALIFIED_LAB=true` only
for this qualification. A running version mismatch returns 409 before config delivery.

## 2. SSH validation keys

Create a dedicated RSA key and public/private directories outside Git:

```sh
mkdir -p secrets/ssh-public secrets/ssh-validation
ssh-keygen -t rsa -b 3072 -f secrets/ssh-validation/id_rsa -N ''
cp secrets/ssh-validation/id_rsa.pub secrets/ssh-public/ztp.pub
```

The unencrypted private key is for unattended lab validation; protect it with file
permissions and do not reuse a personal key. Only the worker mounts the private
key directory. The API mounts the public key and renders a `ztp` user with
`network-admin`, the public key, and a VTY SSH source ACL. It also configures hostname,
management address/default route, optional DNS/NTP, SSH enabled and Telnet disabled.

Populate `secrets/ssh-validation/known_hosts` with the switch host key verified
through your trusted console/enrollment procedure. Merely running `ssh-keyscan`
does not authenticate a key. There is no automatic trust-on-first-use or host-key
verification bypass. If POAP generates a new host key, enroll that key before the
validation deadline; increase the deadline for a supervised initial lab if needed.
Ensure UID 10001 can read the private key and known_hosts inside the worker;
OpenSSH requires appropriately restrictive private-key permissions.

## 3. TLS and release the public bootstrap

Provide `deploy/tls/server.crt` and `server.key`, with a certificate valid for the
controller IP/DNS name. nginx runs as UID/GID 101; allow that identity to read the
key with restrictive ownership/group permissions. The API/worker do not mount the
nginx private key. For a local-only smoke test, a short-lived self-signed certificate
is sufficient if explicitly trusted by the client; it is not automatic firmware trust.

Generate the served script with your API origin and, for a private CA, its public
CA certificate bundle:

```sh
uv run python scripts/release_bootstrap.py \
  --controller https://192.0.2.2 --ca /path/to/public-ca.pem \
  --output deploy/bootstrap
```

The release embeds the public API origin/CA and recalculates Cisco's script MD5
header. It also writes a SHA-256 file. Re-release after every source/URL/CA edit;
never run a formatter against the released file. The `poap/cisco/poap.py` source
contains an unresolved MD5 placeholder and is not itself the deployable artifact.
The manifest records the source revision hash; the released-byte checksum differs
because it embeds site settings. Preserve both for audit.

The public root contains only `poap.py` and `poap.py.sha256`. Never place private
configuration, API tokens or keys there. M2a configs are small immutable database
artifacts served by the authorized API through nginx. Large NOS image serving remains
separate and is not implemented in M2a.

## 4. Configure and start one Compose project

Copy `.env.example` to `.env`, configure the NetBox token and database password as
in the README, then set these example values after editing your files:

```dotenv
ZTP_EXECUTION_MODE=configuration-only
ZTP_ALLOW_UNQUALIFIED_LAB=true
ZTP_CATALOG_FILE=./catalog/site-profiles.json
ZTP_BIND_ADDRESS=192.0.2.2
ZTP_HTTP_PORT=80
ZTP_HTTPS_PORT=443
ZTP_KEA_CONFIG=./deploy/kea/kea-dhcp4.json
ZTP_NGINX_CONFIG=./deploy/nginx/nginx.conf
```

Edit the selected Kea JSON to match your Linux interface, subnet/pool, router/DNS
and server. The repository JSON files use `eth1`; they cannot be tested unchanged
on hosts without that interface. If using the generator, edit its defaults first;
rerunning it otherwise restores the documentation addresses.

```sh
docker compose --profile provisioning config --quiet
# On the actual Linux host, syntax/host-interface check without starting DHCP:
docker compose run --rm --no-deps --entrypoint /usr/sbin/kea-dhcp4 \
  kea-dhcp4 -t /etc/kea/kea-dhcp4.json
docker compose --profile provisioning up --build -d
```

This starts PostgreSQL, migration, API, validation worker, nginx and Kea in one
project. Kea has Linux host networking and binds only the configured interface.
The API also retains a localhost-only developer port. Keep its network exposure
through nginx. Kea is independent of API readiness; unavailable registration fails
closed with bounded client retries. A `web` profile runs nginx without DHCP for
local development. The pinned ISC image is Kea 3.2.0, amd64; ARM Linux is not the
qualified deployment target of this example.

Permit DHCP UDP 67/68 on the intended interface (UDP 67 between relay/server when
relayed), HTTP 80 where selected, HTTPS 443, required DNS/NTP, and worker-to-switch
SSH 22. Restrict these to the ZTP/management networks. Do not start this DHCP scope
on a network with an unintended competing DHCP server.

## 5. Select a bootstrap transport

| Selection | Configuration |
| --- | --- |
| Legacy HTTP bootstrap + HTTPS API | Default Kea JSON and nginx config; script embeds HTTPS API origin/CA |
| Native Secure POAP HTTPS bootstrap | `deploy/kea/profiles/secure-https.json`; qualify NX-OS firmware trust separately |
| Explicit TFTP bootstrap + HTTPS API | `deploy/kea/profiles/legacy-tftp.json` plus Compose `tftp` profile |
| Legacy HTTP API for an isolated lab | Select `deploy/nginx/legacy-http.conf` and release with `--controller http://... --allow-http` |

The default nginx HTTP listener serves public bootstrap only and denies API access.
The HTTPS listener serves bootstrap and authorized API routes. Native Secure POAP
trust is distinct from the CA embedded in the downloaded Python script. Secure
option 43 has no certificate-validation bypass or legacy fallback options. Use it
only after establishing the firmware's trust requirements described in Cisco's
release guide. The generator uses an opaque binary option 43 to retain Cisco's
two-byte suboption lengths; do not remove its `option-def` override.

To enable explicit TFTP fallback, change `ZTP_KEA_CONFIG` to the TFTP profile,
validate it, then:

```sh
docker compose --profile provisioning --profile tftp up --build -d
```

Changing `.env` changes the bind source and recreates the affected service. When
editing the already-mounted Kea file in place, restart Kea or use the tested Kea
reload procedure. Restarting is the simplest lab procedure. Container enablement
alone does not change DHCP options or force a switch to retry bootstrap.

TFTP uses UDP 69 and transfer ports 40000–40100, with maximum block size 1468.
Permit the full configured transfer range through the lab firewall. Its root is
read-only, uploads are disabled, and private config paths are absent. It is never
an API/config/image fallback. Automatic transport downgrade is not implemented.
Stop TFTP explicitly when finished: `docker compose stop tftp`.

## 6. Observe and qualify the switch

The bootstrap requires Python 3, `cli.cli`, `cisco.vrf.set_global_vrf`, management
VRF POAP variables and the qualified inventory/version JSON schema. It discovers
facts, registers, verifies the config length/SHA-256, writes it to bootflash, schedules
`copy bootflash:<file> scheduled-config`, and exits successfully. Native POAP owns
replay, reload if needed and startup save; there is no manual `reload` or image command.

Use the registration's token for config/status requests. A device can report only
`CONFIG_STAGED` or `FAILED`; callback IDs are deduplicated and bound to its config
hash. It cannot declare itself validated. The worker runs even if the callback is
lost and checks serial, model, running version, required running config and startup
config using authenticated SSH. Matching lines are compared in their configuration
context; defaults for SSH/Telnet/interface shutdown are handled explicitly. No raw
configuration or credentials are stored in validation evidence.

Normal M2a outcome: `AUTHORIZED → CONFIGURING/VALIDATING → VALIDATED`, with NetBox
still `staged`. `/api/v1/ztp/status/{id}` includes failure reason/validation evidence.
The worker uses durable leases with stale-worker fencing, retry limits and a deadline;
no SSH reachability, config mismatch or policy drift can activate NetBox. By default,
first validation is delayed 90 seconds, deadline is 1800 seconds, retry interval 30
seconds and maximum attempts 30. These are `ZTP_VALIDATION_*` settings in `.env`; adjust their values before starting the lab.

Record these **physical acceptance tests** before setting `hardware_qualified: true`:

1. Capture DHCP offer/ACK on the real interface and prove the selected bootstrap download.
2. Use an already-target C93180 and verify native scheduled replay plus final management reachability.
3. Confirm SSH host-key trust, identity, version, hostname, address/VRF/default route, SSH ACL and DNS/NTP.
4. Observe VALIDATED with NetBox staged; perform another supervised reboot and confirm saved settings persist.
5. Test missing/corrupt config, denied/revoked inventory, lost API/callback, expired grant and validation timeout.
6. Test explicit TFTP selection on firmware; verify firewall transfer ports and denial of uploads/private files.
7. Restart the stack, preserving PostgreSQL and Kea volumes, and demonstrate no repeated config scheduling.

The bootstrap's bootflash journal records `scheduling` before the native command
and `staged` after success. A repeated staged invocation returns success without
rescheduling. An interrupted `scheduling` journal requires operator investigation;
it does not guess whether the native command ran. Keep journal/config/POAP logs
and inspect native replay state through console before recovery. Failed or validated
attempts do not automatically re-register. Automated retry/reset and full activation
are later milestones; do not delete production workflow history to force a retry.

## Known limits

No real switch was available during implementation. Direct broadcast tests inside
Docker Desktop timed out; relayed DHCP packet exchanges worked on an isolated
internal network. Those prove option bytes and lease handling, not physical broadcast
reachability. SSH responses and native switch CLI were simulated in automated tests.
The initial example username/SSH key syntax and native exit/replay behavior must be
confirmed on your exact NX-OS build, including any initial admin-account requirements.

The database migration preserves M1 records, but M2a adds normalized config fields
and a different manifest. Existing M1 attempts can return drift conflicts; reconcile
those lab planning records explicitly instead of silently reusing them for execution.
M2a rollback refuses to rewrite non-AUTHORIZED lifecycle history into M1 state.
