# Dev server configuration

Server: `192.168.0.95`, project directory `/home/cisco/ztp-controller`.
Management interface `ens2` retains its existing address and default route.
Dedicated ZTP interface `ens3` already has persistent `10.10.10.1/24` configuration.

The server `.env` selects:

- NetBox `https://192.168.0.210`, platform slug `cisco_nxos`.
- `ZTP_NETBOX_VERIFY_SSL=false`, explicitly requested for this lab. The default
  remains `true`; this setting affects only controller-to-NetBox requests.
- A generated database password and a separate `secrets/netbox_token` file.
  `.env` and the token file have mode 0600; container UID 10001 owns the token.
- `ZTP_EXECUTION_MODE=planning-only` and the empty default compatibility catalog.
- nginx bound to `10.10.10.1`, ports 80/443; API developer port remains localhost.
- `ZTP_KEA_CONFIG=./deploy/kea/site-http.json`.

Kea serves only `ens3`, subnet `10.10.10.0/24`, pool
`10.10.10.10 - 10.10.10.50`. Router option 3 advertises `10.10.10.1`; DNS option 6 advertises `8.8.8.8`.
These options were added after the live POAP client rejected offers without them.
The host has not been configured for upstream routing/NAT, so external DNS
reachability is not established. Bootstrap and API use literal IPs on the same subnet.
Option 66 is `http://10.10.10.1`; option 67 is `bootstrap/poap.py`.
The released Python bootstrap uses `https://10.10.10.1` with the generated lab
certificate embedded as its trust anchor. The certificate expires after 90 days;
renew it and re-release the script together.

`deploy/kea/site-tftp.json` is an optional explicit fallback with option 66
`10.10.10.1` and option 67 `poap.py`. To select it, update `ZTP_KEA_CONFIG`, validate
Kea, and start Compose with both `provisioning` and `tftp` profiles. TFTP is not
started by the default deployment.

## Device 27 prerequisites

The read-only NetBox inspection on 2026-09-06 found:

- Name `dc1-pod1-leaf-5`, serial `9297EZA2DPW`, status `staged`.
- Model `N9K-X9564v`, platform `cisco_nxos`.
- Initially no primary IPv4 or context; later inspection confirmed `10.10.10.100/24`
  assigned to its own `mgmt0`, ZTP enabled, and target `10.5(2)F`. Image metadata
  still contained placeholders at that inspection; the subsequent user edit was not rechecked.

Before enabling configuration-only mode, provide a final mgmt0 address outside the
DHCP pool, required provisioning/ztp context, exact running/target NX-OS version,
matching compatibility profile, validation SSH keys and authenticated switch host
key enrollment. See `m2a-lab-guide.md`. Device authorization currently fails closed;
server readiness alone does not mean this switch can be provisioned. No NetBox
records were modified. Local config-file fallback and NOS image serving are not
implemented in this snapshot.

## Operation

Run from `/home/cisco/ztp-controller` on the server:

```sh
sudo docker compose --profile provisioning ps
sudo docker compose logs --tail 100 kea-dhcp4 ztp-api ztp-worker
curl --cacert deploy/tls/server.crt https://10.10.10.1/ready
sudo docker compose stop kea-dhcp4
```

Do not print expanded Compose configuration or `.env` into shared logs because they
contain the database password. Server build/start logs are in `/home/cisco/`.

## Verified deployment

On 2026-09-06 the provisioning Compose profile started successfully. PostgreSQL
and API health checks passed; HTTPS `/ready` returned `{"status":"ready"}` with
live NetBox access. `/health` confirmed planning-only mode with execution disabled.
Kea 3.2.0 validated the ens3 scope and logged successful startup. The HTTP-downloaded
bootstrap SHA-256 matched the released file. The later switch-console evidence below supersedes the initial server-only
transport checks. Configuration replay remains unverified. The TLS-setting unit suite passed 50 tests.


## Switch-console evidence: 2026-09-06 17:07 UTC

The user supplied console output for serial `9D9D49WT46X`, POAP identifier MAC
`52:25:CF:CA:1B:08`. This differs from device 27; do not treat this as validation
of that NetBox record or qualification of a C93180 model/release.

| Stage | Observed result |
| --- | --- |
| DHCP on mgmt0 | Accepted offer from 10.10.10.1; assigned 10.10.10.11/24 |
| Router / DNS | 10.10.10.1 / 8.8.8.8 |
| Script location | http://10.10.10.1/bootstrap/poap.py |
| Download | Successful at 17:07:48, 9598 bytes |
| Embedded and calculated MD5 | Both 99852cf5281787811caf1354cdf7e7b0 |
| Execution | Started after MD5 validation, then failed at 17:07:49 |
| Full provisioning / image installation | Not demonstrated; M2a does not install images |

The earlier missing options 3/6 caused explicit rejection by this POAP client.
Both are now present in the HTTP and optional TFTP examples. The earlier lease
10.10.10.10 belonged to MAC 52:54:00:4e:8e:eb and is not evidence for this console's
switch. DHCP/download are independent of the NetBox configuration prerequisites.

Reusable files and reconstruction instructions are in
[the lab example](../examples/lab-192.168.0.95/README.md). Generated script bytes
change when its source, URL or embedded certificate changes; the recorded checksum
is evidence for this run, not a value to hard-code into another release.
