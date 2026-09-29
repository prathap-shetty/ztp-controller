# Cisco NX-OS POAP implementation plan

Date: 2026-09-06. Status: M1 and M2a software implemented; M2a physical-switch qualification remains pending. See [implementation status](implementation-status.md).

## 1. Baseline and scope

This plan follows `greenfield-dc-ztp-plan.md`, the companion architecture document. The repository includes the M1/M2a application, bootstrap, locked dependencies, migrations, tests and Compose network-service profiles. Preserve its FastAPI, InventoryProvider, CiscoNxosAdapter, and separate artifact-service architecture.

Deliver a controlled greenfield workflow for pre-created NetBox devices: authorize a staged switch, resolve intent, provision approved software and initial management configuration, independently validate the result, and then activate it in NetBox. Store operational workflow records locally, not a second authoritative inventory.

Start with one qualified Nexus 9000 model family, IPv4, and the management port. Exact models, factory releases, and target images remain deployment inputs; the example model in the original document is not a confirmed support requirement. Defer Infrahub implementation, other vendors, fabric-wide EVPN/VXLAN configuration, UI, automatic downgrades, and unqualified multi-hop upgrades.

**First delivery stops at registration → NetBox authorization → normalized intent → deterministic manifest. It must not install software or change switch configuration.**

## 2. Decisions and gaps resolved

| Topic | Proposed implementation decision |
| --- | --- |
| Eligibility | Require exactly one serial match, `staged`, explicit `provisioning.ztp_enabled: true`, supported vendor/platform/model, and complete validated intent. Missing enablement means deny; both documents use this policy. |
| Persistence | PostgreSQL with SQLAlchemy and Alembic; persist attempts, events, artifact references, intent snapshots, and validation jobs. NetBox stays authoritative for inventory. |
| Background work | A small durable worker polling database jobs with leases; do not depend on FastAPI process memory or request background tasks for recovery. No large workflow engine needed. |
| Upgrade policy | Only explicitly approved source/model/target transitions. A version difference is insufficient authorization to install. Unknown paths fail before mutation. |
| Reboot ownership | Follow release-qualified native POAP installation and scheduled configuration replay. Do not assume the Python bootstrap runs again after a successful replay. |
| Success authority | Device callbacks provide evidence and progress. Only controller validation and successful inventory reconciliation can complete a run. |
| Initial configuration | Hostname, management address/prefix, management VRF routing, approved SSH access, DNS/NTP and required management policy. Extend only with explicit deployment intent. |
| Deployment | One Linux Docker Compose project: Kea DHCPv4, API, worker, PostgreSQL, nginx and optional TFTP. Existing NetBox and DNS/NTP are external. |
| DHCP | ISC Kea is the proposed default; ISC DHCP/dhcpd is EOL and only a mutually exclusive legacy override if specifically required. |
| Fallback | Optional read-only TFTP for bootstrap only; operator-selected mode is the baseline, automatic fallback requires hardware evidence. |
| Transport | Qualify bootstrap retrieval, API access, and bulk transfer independently on the oldest factory release. No global certificate-verification bypass. |

Cisco documents configuration replay after the new image boots, followed by saving startup configuration. It also distinguishes legacy DHCP bootstrap options from Secure POAP introduced in 10.2(3)F, using IPv4 option 43. Therefore, generic HTTP bootfile recipes and an ordinary install/reload loop are insufficient specifications. [Cisco POAP guide](https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/102x/configuration/fundamentals/cisco-nexus-9000-nx-os-fundamentals-configuration-guide-102x/m-using-poap.html)

### Docker Compose and transport implementation contract

Use the service/network/storage boundaries in the [design document](greenfield-dc-ztp-plan.md#single-stack-deployment-and-dhcp). Implement `compose.yaml` as a single-host Compose project, not a Docker Swarm deployment. Keep separate containers for `kea-dhcp4`, `ztp-api`, `ztp-worker`, `postgres`, `nginx`, and optional `tftp` under a `tftp` profile. Do not embed an additional NetBox instance.

ISC lists the old DHCP product as EOL and provides supported Kea releases. Select and pin a supported Kea build, preserving the user's ISC-server requirement. Do not copy dhcpd examples verbatim into Kea JSON. [ISC download page](https://www.isc.org/download/)

Planned deployment files:

- `deploy/kea/kea-dhcp4.json`: dedicated interface, subnet/pool, persistent memfile leases, infrastructure/static-IP exclusions and reservations as appropriate.
- `deploy/kea/profiles/`: validated `secure-https`, `legacy-http`, and `legacy-tftp` boot-option examples; deploy only the selected class/scope configuration.
- `deploy/nginx/`: API proxy, TLS configuration and separate public bootstrap/private artifact locations.
- `deploy/tftp/`: selected read-only daemon configuration, explicit bind address and UDP transfer-port range; no upload support.
- `deploy/docker/`: reproducible application/DHCP/TFTP builds as needed, pinned images, health checks and minimum capabilities.
- `deploy/secrets/README.md`: required secret mounts and provisioning steps, with no real credentials committed.

Run Kea and optional TFTP using Linux host networking and explicit ZTP-interface/address binding. Use normal private Compose networking for API, worker and database, and publish nginx only on intended host addresses. Verify required raw-socket/bind capabilities for the chosen DHCP build rather than granting every container privileged mode. Host-network services do not use Compose port mappings. A routed-relay override may be added after qualification. Mac Docker Desktop is for controller development; qualify physical DHCP/TFTP on Linux. [Docker host networking](https://docs.docker.com/engine/network/drivers/host/)

Persist PostgreSQL, Kea lease files and immutable generated artifacts independently. Publish artifacts atomically, mount serving roots read-only and mount no private configs into TFTP. Run migrations before API/worker readiness. Use restart policies and durable worker leases; DHCP may remain available while the controller is restarting. Keep DNS/NTP and final management routes as documented prerequisites. Pin versions at implementation time and test `docker compose config`, service readiness, lease retention and full-stack restart recovery.

Use Kea's configuration validator before startup, then packet-capture the actual DHCP exchange. Confirm boot server/name fields, options 66/67 or secure option 43 encoding, client-class matching and any required option emission with the selected NX-OS release. Kea supports configurable options and boot fields, but successful syntax validation alone does not certify the switch's interpretation. [Kea DHCPv4 manual](https://kea.readthedocs.io/en/stable/arm/dhcp4-srv.html)

Keep fallback policy server-controlled in the compatibility profile. Record primary transport, allowed fallback, activation mode (`operator` or `qualified-auto`), allowed failure conditions and retry budget. Script retrieval occurs before registration; DHCP profile selection must therefore come from configured scope/class/reservation, not a manifest that the switch has not yet fetched. Do not assume serial identity is available as a DHCP reservation key.

For MVP, TFTP serves only the pinned non-secret bootstrap and its required compatibility checksum files. Bootstrap retrieval uses firmware behavior; it cannot be rescued by Python that has not downloaded. Implement and document explicit DHCP profile selection/retry first. Test automatic legacy fallback only where the firmware supports it, and do not downgrade certificate failures by default. HTTP(S) remains required for registration/status and private configs. Large NX-OS binaries over TFTP are deferred until transfer-size, block-size, timeout and checksum behavior is qualified; no blanket TFTP fallback for all traffic is promised.

Scope public TFTP access by network policy; it cannot enforce per-attempt bearer tokens. Keep private HTTP(S) artifact grants unchanged. Exercise UDP 69 and the configured transfer-port range through the actual firewall. Starting the optional container alone must not change DHCP policy. Log fallback mode and outcome, and ensure a working TFTP server never bypasses API authorization.

Review both user-supplied Cisco references while implementing: the [POAP source directory](https://github.com/datacenter/nexus9000/tree/master/nx-os/poap) and [DevNet POAP overview](https://developer.cisco.com/docs/nx-os/poap/#poap-poweron-auto-provisioning). Pin the reviewed script revision and preserve licensing. Their general transport support is a starting point for the per-release qualification matrix, not evidence of an automatic transport-failover sequence.

## 3. Qualification gate before hardware implementation

Create a tested compatibility record for each supported combination:

- Switch PID/model, standalone NX-OS mode, factory version, target version, image filename, size, and trusted SHA-256.
- Vendor-supported upgrade path, intermediate releases if needed, BIOS/EPLD implications, bootflash capacity, and expected reload duration. Reject unsupported paths in MVP rather than guessing installation commands.
- On-box Python interpreter, available CLI/transfer modules, JSON output formats, hashing capability, management-VRF API connectivity, and TLS trust behavior.
- DHCP server/relay, subnet, gateway, DNS/NTP, bootstrap options and script path; distinguish temporary DHCP addressing from final management addressing.
- Script retrieval transport, artifact transfer transport, script checksum format, native install/replay sequence, and POAP timeout behavior.

Use an isolated physical lab switch with console access for these checks. Simulator tests validate controller logic but cannot certify real upgrade or firmware behavior. Record actual observed commands and output fixtures in the compatibility record before coding the destructive path.

Cisco's published sample contains release-dependent installation branches and scheduled-config handling. Review and pin a specific upstream revision, retain its licensing information, and adapt the necessary lifecycle behavior. Do not execute a moving upstream script or inherit its example credentials. Its embedded MD5 convention is a POAP compatibility mechanism; retain separate SHA-256 verification for approved artifacts. [Cisco sample script](https://raw.githubusercontent.com/datacenter/nexus9000/master/nx-os/poap/poap.py)

## 4. Component and data contracts

### Inventory provider

Implement typed `get_device_by_serial`, `get_device_intent`, `get_management_ip`, and status-update methods. Return explicit not-found, duplicate, unavailable, and invalid-intent errors. Add a guarded activation operation or equivalent service-level reconciliation around status updates.

The NetBox implementation queries devices by serial, requires exactly one result, and resolves manufacturer, platform, model, status, primary/management IP assignment and rendered Config Context. Check the deployed NetBox API schema and permissions before fixing request fields. Follow pagination and retain IP prefixes. Gateway and management VRF must come from an explicit validated policy; do not infer a gateway from the primary IP. NetBox exposes REST filtering, token authentication and PATCH operations; integration tests must target the site's installed version. [NetBox REST documentation](https://netbox.readthedocs.io/en/stable/integrations/rest-api/)

Normalize to the document's `DeviceIntent`, extending it with prefix-aware addresses, schema version and intent revision/hash. Missing software target, malformed checksums, unsupported models, inconsistent IP ownership, or conflicting context fail closed. Adopt one canonical policy location rather than silently combining competing flags.

### Manifest and artifacts

Define a versioned manifest containing:

- Attempt ID, device ID, canonical serial, intent hash, template revision, bootstrap revision and compatibility-profile ID.
- Target software and ordered approved actions; `upgrade_required=false` when the normalized observed version already matches.
- Opaque image/config artifact IDs, byte sizes and SHA-256 values.
- Expected final management endpoint, validation profile and bounded retry/reboot budgets.

Separate this deterministic desired-state payload from a delivery envelope containing expiring URLs, tokens and timestamps. Repeated registration with identical intent must produce the same plan hash even if access tokens rotate. Persist the snapshot so a NetBox edit cannot silently change an in-progress run.

Render Jinja2 with `StrictUndefined`, typed input validation, stable ordering and newline-injection checks. Store an immutable per-attempt config and checksum. Secret values come from deployment-managed secret storage, not NetBox Config Context or logs. Limit initial scope to management bootstrap; verify required running and saved configuration semantically instead of comparing full CLI text byte-for-byte.

Serve bulk images through nginx/object storage; FastAPI resolves authorization and references. Scope config downloads and artifact grants to an attempt and expiry. Never accept arbitrary filesystem paths, image filenames or destination URLs from a switch. A failed or revoked run must lose future private artifact access. Public non-secret bootstrap files, including the TFTP root, are governed by network policy rather than attempt tokens.

### API contract

| Endpoint | Behavior |
| --- | --- |
| `GET /health` | Liveness only; add `/ready` for required dependency readiness. |
| `POST /api/v1/ztp/register` | Validate observed facts, authorize, atomically create/resume an attempt, return manifest/envelope. |
| `GET /api/v1/ztp/config/{authorized-reference}` | Deliver or redirect to the immutable authorized configuration. |
| `POST /api/v1/ztp/status/{provisioning_id}` | Accept scoped, deduplicated device events; prohibit device-requested COMPLETE or inventory changes. |
| `GET /api/v1/ztp/status/{provisioning_id}` | Return authorized operational state with secrets removed. |

Use structured errors: malformed data 422, policy denial 403, conflicting active attempt/intent 409, unavailable inventory 503. Rate-limit registration and avoid leaking inventory detail in denial responses. Device requests carry serial/model/version/MAC as observed facts, not proof of authentication. Issue scoped attempt credentials after network-constrained authorization; operator read/retry access uses separate authentication.

## 5. State, idempotency and recovery

Use the following expanded state graph; download/install states are skipped for an already-correct image:

```text
DISCOVERED → AUTHORIZED → ARTIFACTS_READY
  → IMAGE_DOWNLOADING → IMAGE_VERIFIED → INSTALLING
  → REBOOTING → CONFIGURING → VALIDATING
  → VALIDATED → ACTIVATION_PENDING → COMPLETE

Any pre-activation provisioning stage → FAILED
Already at target: ARTIFACTS_READY → CONFIGURING → VALIDATING
```

These are controller abstractions. CONFIGURING can represent native replay occurring after an expected reload; do not require a callback from every on-box phase. The same-image path may still reload if the qualified POAP replay method requires it.

Persist attempt ID, device ID, intent/manifest hashes, event IDs, state version, timestamps, deadline, retry counters, last observations, failure reason, and job lease. Enforce at most one active attempt per device with a database constraint and transactional updates. Reject invalid or out-of-order transitions and make repeated event IDs no-ops.

Before installation, download and verify both image and final configuration, then durably record the planned transition and local checkpoint. Re-registration reuses the active attempt only after policy and identity checks. Reconcile actual version, boot state and artifact hashes before deciding whether any action remains necessary. A local checkpoint supports recovery but never proves an installation succeeded.

After normal native replay, the durable controller worker connects to the intended management address and verifies the device. This removes the dependency on a second bootstrap invocation. If power fails early and POAP restarts, the same-attempt registration path handles it. If configuration exists but validation cannot connect, retain failure evidence and require an explicit recovery action; do not erase startup configuration automatically.

Pin intent for the run. Policy revocation stops new grants/actions; material intent drift blocks activation and requires reconciliation. Bounded transient retries use backoff and jitter. Checksum mismatch, unsupported upgrade, identity mismatch and exhausted reboot budget stop automatically. Preserve current boot images and console/POAP diagnostics; do not promise automatic rollback for every install failure.

NetBox and PostgreSQL cannot share an atomic transaction. After validation, persist `ACTIVATION_PENDING`, recheck eligibility and intent, then PATCH only status. Read back the outcome and mark COMPLETE after confirmation. On a lost response, reconcile before retrying; never rerun POAP merely because activation failed. Retry inventory synchronization durably. Detect external lifecycle changes and escalate conflicts; never overwrite unrelated status changes or automatically downgrade an active device. Document the remaining race with external NetBox writers and enforce an operational ownership policy for staged-device activation.

## 6. Implementation milestones and acceptance gates

### M0 — Contracts and lab inputs

Write schemas, policy decisions, compatibility matrix and sample sanitized NetBox fixtures. Record the Linux deployment host/interface, Kea version, DHCP scope or relay, reachable server IPs and selected bootstrap/fallback profiles. Establish management connectivity, addressing ownership and verification credentials. Select actual model/releases and consult their installation guides before qualifying image transitions.

**Exit:** one explicit supported profile and agreed initial config/validation fields; unknown combinations are denied. Controller development may begin with fixtures while physical qualification proceeds.

### M1 — Registration and deterministic planning

Scaffold FastAPI settings, provider factory, typed models, Cisco adapter, persistence migrations and test fixtures. Add a Compose development subset for API/worker/database with physical DHCP disabled. Implement provider normalization and authorization tests first, then registration and manifest generation. NetBox access is read-only in this milestone. Use a test client or discovery-only bootstrap stub; keep installation actions disabled server-side.

**Exit:** known staged/enabled switch gets a deterministic manifest; unknown, duplicate, non-staged, disabled, incompatible and incomplete devices are rejected. Provider outages fail closed. Concurrent/repeated registration creates one attempt, and restart preserves it. No upgrade, configuration application or NetBox status writes occur.

### M2a — Native POAP, configuration-only path

Build the qualified bootstrap, script-checksum release process, Kea/nginx/TFTP Compose services and transport profiles, immutable rendering and validation worker. Validate Kea configuration, DHCP packet contents, primary download and operator-selected TFTP bootstrap on the Linux lab host before applying configuration. On a switch already at target, retrieve intent/config, use native POAP replay, and prove the final management endpoint survives boot. Keep automatic inventory activation disabled until M3.

**Exit:** physical switch reaches the expected version and management config without image installation. Required startup settings persist after another reboot. Missing config, denied access and lost connectivity produce bounded failures while inventory remains staged. TFTP serves no secrets, cannot bypass API denial, and works through the configured UDP transfer range. Record automatic fallback as unsupported until a release-specific test proves it; test that certificate errors do not trigger an unapproved downgrade.

### M2b — Image installation and reload recovery

Add approved catalog transitions, image-space prechecks, partial-download handling, SHA-256 validation, native installation, checkpoint/reconciliation and reboot budgets. Set timeouts from measured image transfer and hardware boot behavior.

**Exit:** one supported factory-to-target upgrade succeeds physically; an already-target switch skips installation. Inject interrupted download, bad digest, insufficient space, controller restart, lost callback, and reboot/power interruption at safe lab checkpoints. No repeated-install loop or accidental deletion of the boot image occurs. Unsupported paths are rejected before mutation.

### M3 — Independent validation and activation

Read serial/model/version over authenticated management access (initially SSH with a release-tested Scrapli driver). Confirm hostname, management IP/prefix/VRF/route, required access controls, intended services and saved configuration. Scope SSH trust enrollment to the isolated bootstrap environment and record the learned identity. Obtain credentials from secret storage; a successful TCP connection or device success callback is insufficient.

Implement durable activation reconciliation and audit results. Do not permit callback bodies to select validation destinations: derive them from the pinned inventory intent.

**Exit:** successful validation activates exactly the intended NetBox device; any provisioning/validation failure leaves it staged. Simulate NetBox outage, timeout after successful PATCH, external status changes and worker restarts without repeating switch installation.

### M4 — Pilot and operational handover

Harden the complete Docker Compose project delivered in M2a: Kea, API, worker, database, nginx and optional TFTP. Keep DHCP on an explicitly configured provisioning-network interface/relay; application containers alone do not provide the required layer-2 reachability. Add migrations, backups, log redaction, metrics and an operator runbook covering profile selection, lease persistence, host firewall rules, TFTP enable/disable and recovery. Test full-stack restart, DHCP interface isolation and worker reachability after the final management-address transition.

Track counts and duration by stage, failure codes, stale attempts, artifact transfer errors and inventory-sync backlog. Test token expiry across long upgrades, artifact access isolation and several simultaneous devices at an agreed pilot batch size. Set concurrency from observed DHCP capacity, artifact bandwidth and NetBox load.

**Exit:** a batch provisions unattended with durable outcomes and bounded retries; an operator can diagnose and explicitly retry a failed attempt using documented console recovery where necessary.

## 7. Proposed repository layout

```text
app/
  main.py, settings.py
  api/ztp.py
  models/                  # identity, intent, manifest, workflow, errors
  inventory/base.py, factory.py, netbox.py
  vendors/base.py, cisco_nxos.py
  services/                # authorization, planning, rendering, reconciliation
  persistence/             # database models, repositories, migrations
  workers/                 # validation and inventory activation jobs
poap/cisco/                 # qualified bootstrap and compatibility helpers
templates/cisco/nxos_initial.j2
catalog/                   # approved image metadata; no NOS binaries in Git
compose.yaml               # one Linux Compose project, optional tftp profile
deploy/kea/                # ISC Kea configuration and boot transport profiles
deploy/nginx/              # proxy and artifact serving
deploy/tftp/               # read-only bootstrap daemon configuration
deploy/docker/             # reproducible container builds
deploy/secrets/README.md   # secret mount instructions, no credentials
tests/unit/
tests/integration/
tests/fixtures/nxos/
tests/hardware/            # opt-in lab procedures, never default CI execution
docs/compatibility-matrix.md
docs/operations-runbook.md
pyproject.toml
README.md
.env.example
```

Keep provider/vendor interfaces small. Add the Infrahub provider only when its actual contract is known. Pin application dependencies at implementation time; keep switch-side runtime dependencies separate from server-side Python.

## 8. Readiness inputs and completion criteria

Before hardware work, supply exact switch PIDs and factory versions, approved target binaries/checksums and upgrade documentation, NetBox version/access and representative device/context records, Linux host/interface and ZTP subnet/DHCP or relay details, selected Kea build, bootstrap transport/fallback policy and TFTP port range, final management addressing, transport trust configuration and SSH validation access. These do not prevent M1 implementation using explicit fixtures.

The final acceptance demonstration starts with a factory-default qualified Nexus and ends with approved NX-OS, intended persisted management configuration, independently collected validation evidence, a COMPLETE controller attempt and NetBox status active. Repeat for a switch already at target and for a deliberately failing attempt that remains staged. All attempts must remain explainable after a controller restart.

Recommended implementation order: **M1 controller contract first; then M2a configuration-only POAP, M2b upgrade/recovery, M3 activation, and M4 batch operations**, with M0 qualification completed before the corresponding hardware work.
