# ZTP Lite and Mac PoC

**ZTP Lite** is the simple Linux deployment for a controlled management network: local YAML, NetBox or InfraHub, NX-OS upgrade and configuration, and a token-protected status dashboard. It keeps TLS and SSH host-key validation disabled and uses manual post-boot verification. See the [ZTP Lite user guide](docs/ztp-lite-user-guide.md). Use `compose.lite.yaml` with `.env.lite`.

**Mac PoC** uses Docker Desktop and native DHCP on the USB-Ethernet adapter. See the [Mac PoC guide](examples/mac-en7/README.md).

# ZTP controller

Cisco NX-OS POAP controller with NetBox inventory, PostgreSQL workflow state and
one Docker Compose project. **M1 planning and M2a configuration-only software are
implemented.** Image installation and NetBox activation remain disabled.

M2a targets a switch already running the approved image. It renders and stores an
immutable configuration, serves it through an authorized API, schedules native POAP
replay and independently validates running/startup state over SSH. A successful
attempt stops at `VALIDATED`; NetBox remains `staged`.

**Start with the [M2a lab guide](docs/m2a-lab-guide.md)** for editable C93180/NX-OS
10.5 examples, Linux networking, Kea profiles, TLS, keys and optional TFTP. Physical
switch replay is not yet qualified; the software and network-container tests are
listed in [implementation status](docs/implementation-status.md).

## Components

| Component | Role |
| --- | --- |
| FastAPI | Registration, authorization, deterministic manifests, private config and status |
| NetBox provider | Read-only exact serial lookup, staged/enablement policy, management IP ownership |
| Cisco adapter | Approved model/source/target checks; already-target gate for M2a |
| PostgreSQL / Alembic | Attempts, immutable configs, audit events, expiring grants and leased validation jobs |
| Worker | Expired grant cleanup and bounded read-only SSH validation |
| nginx | HTTPS API proxy and public bootstrap; optional explicit legacy HTTP API |
| ISC Kea 3.2.0 | DHCPv4 and qualified boot-option profiles on a dedicated Linux interface |
| Optional TFTP | Read-only public bootstrap fallback, never private configuration or images |

The default execution mode is `planning-only`; the default catalog is empty and
denies every device. Configuration-only execution requires a populated site catalog,
SSH public key, initial configuration intent and either hardware qualification or
explicit lab opt-in. Only the worker mounts the validation private key. SSH host-key
verification is mandatory. There are no automatic transport downgrades.

## NetBox intent

Pre-create a device with uppercase serial, Cisco manufacturer slug `cisco`, NX-OS
platform slug (default `nxos`), `staged` status and primary IPv4 assigned to its own
`mgmt0`. Configure rendered Config Context:

```yaml
provisioning:
  ztp_enabled: true
ztp:
  target_nxos: "10.5(2)F"  # editable example, exact running release for M2a
  image:
    filename: "YOUR_APPROVED_IMAGE.bin"
    sha256: "YOUR_64_CHARACTER_LOWERCASE_SHA256"
  management:
    gateway: "192.0.2.1"
    vrf: "management"
    interface: "mgmt0"
  initial_configuration:  # required for M2a; optional in planning mode
    dns_servers: [192.0.2.1]
    ntp_servers: [192.0.2.1]
    ssh_sources: [192.0.2.0/24]
```

The address/prefix comes from IPAM. Replace documentation IPs and the full switch
PID/release with actual values. No real NX-OS image metadata is supplied. Use the
[catalog instructions](catalog/README.md) and
[editable profile](catalog/c93180-nxos105.example.json). Unknown paths, duplicate
serials, invalid/missing intent, non-staged devices and disabled policy fail closed.
Unrelated NetBox Config Context is not persisted or returned.

## Planning-only development stack

1. Copy `.env.example` to `.env`; set a unique URL-safe PostgreSQL password and
   NetBox installation root URL **without `/api/`**.
2. Put a read-only token in `secrets/netbox_token`. Select `Token` or `Bearer` to
   match the site's token type. See [secret permissions](deploy/secrets/README.md).
3. Populate a site catalog and set `ZTP_CATALOG_FILE`.

```sh
docker compose config --quiet
docker compose up --build -d
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
```

Without profiles, Compose starts PostgreSQL, migrations, API and worker only.
`web` adds nginx. `provisioning` adds nginx and Linux-host-network Kea; `tftp` adds
the optional TFTP server. Follow the lab guide before enabling network services.
No additional NetBox, DNS or NTP instance is deployed.

`/health` reports execution mode and liveness. `/ready` checks storage/schema and
NetBox read access; an empty catalog may be ready but authorizes nothing. The API's
developer port is bound to localhost and does not trust forwarded client-IP headers.
Rate limits therefore aggregate requests arriving through nginx under its source
address; tune the shared limit to your lab batch size until proxy-aware accounting
is implemented. Stop with `docker compose down`; avoid removing data volumes when
attempt/lease history is needed.

## API

| Endpoint | Behavior |
| --- | --- |
| `POST /api/v1/ztp/register` | Authorize and atomically create/resume an immutable plan |
| `GET /api/v1/ztp/status/{id}` | Attempt state, failure reason and sanitized validation evidence |
| `GET /api/v1/ztp/config/{id}` | Scoped immutable configuration, M2a only |
| `POST /api/v1/ztp/status/{id}` | Deduplicated `CONFIG_STAGED` or `FAILED` callback, M2a only |

Registration uses the [observed facts example](tests/fixtures/observed.json).
The response includes attempt ID, stable plan hash, manifest and an expiring
`status_token`. Send it as `Authorization: Bearer <token>` for status/config/events.
The token is attempt-scoped; it cannot activate NetBox or submit arbitrary states.
Config fetch and callbacks recheck current policy and intent. The image metadata
in a manifest is never an installation instruction in M2a.

Repeated unchanged registrations reuse the attempt. Changes return 409 without
replacing history. FAILED/VALIDATED attempts cannot automatically re-register.
There is no automatic retry/reset endpoint. Planning manifests have no actions;
configuration-only manifests have just `stage-config`. Device callbacks cannot set
VALIDATED; the worker determines it from authenticated observations.

## Development and tests

Use Python 3.12 and uv:

```sh
uv sync --frozen
uv run ruff check app migrations tests scripts poap
uv run ruff format --check app migrations tests scripts poap
uv run pytest tests/unit -q
```

Integration tests need a disposable PostgreSQL database:

```sh
docker run -d --name ztp-test-db \
  -e POSTGRES_USER=ztp_test -e POSTGRES_PASSWORD=local-test-only \
  -e POSTGRES_DB=ztp_test -p 127.0.0.1:55432:5432 postgres:17-bookworm
# Wait until: docker exec ztp-test-db pg_isready -U ztp_test -d ztp_test
export TEST_DATABASE_URL='postgresql+psycopg://ztp_test:local-test-only@127.0.0.1:55432/ztp_test'
uv run pytest -q
docker rm -f ztp-test-db
```

`TEST_DATABASE_URL` must be disposable: fixtures drop controller tables and reapply
migrations. Without it integration tests skip. CI runs against PostgreSQL. Network
probes in `tests/support/network_probe.py` are only for an isolated Docker test
network and must never be run on a live ZTP LAN.

For a local API, export `ZTP_DATABASE_URL`, `ZTP_NETBOX_URL`, a token source and
`ZTP_CATALOG_PATH`, then run `uv run alembic upgrade head` and
`uv run uvicorn app.main:create_app --factory --no-proxy-headers`. The Python
application does not automatically load `.env`; Compose does.

Update dependencies with `uv lock`, then regenerate the container export:

```sh
uv export --frozen --no-dev --no-emit-project --format requirements-txt \
  --output-file requirements.lock
```

See the [design](docs/greenfield-dc-ztp-plan.md),
[roadmap](docs/cisco-nxos-poap-implementation-plan.md), and
[lab qualification checklist](docs/m2a-lab-guide.md#6-observe-and-qualify-the-switch).

The [reproducible lab examples](examples/lab-192.168.0.95/README.md) include the
working ens3 DHCP settings, sanitized environment file, bootstrap release commands
and switch-console evidence. DHCP, HTTP download and MD5 validation passed;
script execution and full provisioning remain unqualified.

## Image upgrade lab (M2b)

The opt-in `upgrade-and-configure` mode adds authenticated image streaming from a
read-only host folder, bootflash SHA-256 verification, native POAP installation and
post-reboot validation. See the [upgrade lab guide](docs/m2b-upgrade-lab-guide.md).
The N9K-C93180YC-FX3 10.4(4) → 10.5(4)M path still requires Cisco matrix approval
and physical qualification. No real-device upgrade has been performed.

## Local inventory without NetBox

Set `ZTP_INVENTORY_PROVIDER=local-yaml` and use
`docker compose -f compose.yaml -f compose.local.yaml ...` to remove NetBox secret
requirements. Copy `inventory/devices.example.yaml` into your private inventory
directory, replace its values, and mount it with `ZTP_LOCAL_INVENTORY_DIR`.
See [local YAML inventory](docs/local-yaml-inventory.md) for the full setup and
revocation behavior. The default local inventory is empty; no device is authorized.

## ZTP Lite (Linux)

For an isolated new-build lab, [ZTP Lite](docs/ztp-lite.md) uses standalone
`compose.poc.yaml`: HTTP bootstrap/API, local YAML or NetBox, one fleet catalog,
password-based switch configuration, and manual console validation. No TLS/SSH keys
or known_hosts are required. Physical upgrade qualification is still pending.

Start with the [step-by-step two-interface VM lab user guide](docs/poc-lab-user-guide.md).

For a Mac USB Ethernet adapter, use the [en7 example](examples/mac-en7/README.md):
`compose.poc.mac.yaml` runs HTTP/API/database in Docker Desktop while native dnsmasq
serves DHCP on en7. Docker Desktop cannot expose en7 directly to the Kea container.

## InfraHub inventory

Use `ZTP_INVENTORY_PROVIDER=infrahub` with the [InfraHub setup guide](docs/infrahub-inventory.md). The provider reads `DcimDevice` identity and explicit ZTP attributes through GraphQL; it never writes to inventory.
