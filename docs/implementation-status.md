# Implementation status

## M1 — Registration and deterministic planning

Implemented: FastAPI factory/settings; read-only NetBox provider; typed identity,
management and software intent; authorization and catalog checks; Cisco adapter;
planning-only manifests; PostgreSQL schema/migrations; idempotent registration;
authorization audit events; hashed expiring status grants; rate limits; maintenance
worker; local Compose subset and test fixtures.

M1 intentionally has no switch commands, executable actions, downloadable configs,
status mutation endpoint or inventory writes. The catalog is empty by default.
Only fields consumed by M1 are normalized and snapshotted; future configuration
fields must extend the typed contract and drift hash before M2a rendering.

Verified on 2026-09-06:

- 49 tests passed with an actual disposable PostgreSQL container; no skipped tests.
- Concurrent registrations produced one attempt and one authorization audit event.
- Migration upgrade/downgrade/upgrade passed; `alembic check` reported no schema drift.
- Ruff lint/format checks and frozen dependency-lock validation passed.
- The Docker image built with hash-checked dependencies; Compose configuration validated.
- The Compose stack started its migration job, API, worker and database against a
  read-only synthetic NetBox server. Readiness, registration and authenticated
  status worked before and after restarting PostgreSQL/API/worker, preserving the
  same attempt ID, plan hash and original status grant.

The suite emits two upstream Starlette/AnyIO test-client deprecation warnings;
there are no failing checks. The test NetBox server is synthetic: deployed NetBox
API/schema/token compatibility and physical switch behavior are not established
by these tests. Disposable verification containers were removed after testing.

## M2a — Configuration-only software

Implemented:

- Explicit configuration-only mode, same-image gate and per-profile replay method;
  no image installation or NetBox writes.
- Typed DNS/NTP/SSH source intent, strict Jinja2 rendering, validated RSA public key,
  immutable PostgreSQL config artifact and scoped download with SHA-256/size checks.
- Original Python 3 bootstrap with management-VRF sockets, strict HTTPS, bounded
  retries, no redirects/downgrades, native scheduled-config handoff and durable
  bootflash journal preventing blind rescheduling after an ambiguous interruption.
- CONFIG_STAGED/FAILED callbacks with deduplication; durable validation jobs,
  lease fencing, timeouts and read-only strict-host-key SSH validation of identity,
  release, running config and startup config. Success stops at VALIDATED/staged.
- One Compose project with nginx, ISC Kea 3.2.0 and optional read-only TFTP profiles;
  digest-pinned network images, restricted capabilities and persistent leases.
- Editable C93180/NX-OS 10.5 catalog and Linux eth1/192.0.2.0/24 deployment examples;
  bootstrap release tooling, DHCP profile generator and detailed lab guide.

Verified on 2026-09-06:

- 68 unit/PostgreSQL integration tests passed with no skips (two upstream test-client
  deprecation warnings). Coverage includes the same-image gate, immutable config,
  token/policy revocation, callback deduplication, no activation, lease fencing,
  bounded validation, lost callbacks, checksum rejection, ambiguous replay
  interruption and certificate failure without downgrade.
- Alembic reported no schema drift. Lint/format, lock consistency and Compose
  profile configuration checks passed.
- All three Kea profiles passed syntax validation with the placeholder interface
  adapted to the test container. Relayed DISCOVER/OFFER/REQUEST/ACK exchanges
  verified HTTP, TFTP and secure option-43 bytes on an isolated internal network.
  Kea lease data survived service restart.
- Read-only TFTP transferred the released script with matching SHA-256, negotiated
  blocks and response ports within 40000–40100. Uploads and absent private configs
  were rejected.
- The HTTPS Compose stack built and served a public bootstrap, denied HTTP API
  access by default, and ran the bootstrap against live registration/config/event
  endpoints with simulated native CLI. Attempt ID, plan hash and status grant
  persisted after PostgreSQL/API/worker restart.

The Kea binary needs
DAC_OVERRIDE with the chosen root/drop-all-capabilities setup because of its
packaged kea-owned executable permissions. The nginx read-only filesystem uses
/tmp for all temporary paths. The secure Kea profile uses a binary option-43
override, verified at packet level, to preserve Cisco's two-byte suboption lengths.

**Hardware acceptance is pending.** No C93180 was contacted. Tests simulate native
CLI/SSH responses and use real containerized services for API, PostgreSQL, DHCP and
TFTP. Direct Docker Desktop broadcast DHCP timed out; synthetic relayed DHCP works.
Neither that exchange nor syntax validation certifies physical Linux broadcast
reachability, native Secure POAP trust, replay/reload or saved configuration after
a real reboot. See [the lab guide](m2a-lab-guide.md) for the remaining gate.

## Remaining milestones

- M0/M2a physical profile qualification on the exact C93180 PID and NX-OS release.
- M2b: approved image transfer, integrity checks, native install and reload recovery.
- M3: full lifecycle, explicit retry management and durable NetBox activation reconciliation.
- M4: production database secret mounts, richer metrics, recovery operations and switch pilot.

The M2a migration retains one attempt per device. Failed or validated attempts need
operator reconciliation; no automatic reset or startup-config erasure is exposed.
The worker performs read-only validation, not configuration or software installation.

## 2026-09-06 live lab transport result

On server 192.168.0.95, ens3/10.10.10.1 serves 10.10.10.10–50. Switch console
9D9D49WT46X confirmed lease 10.10.10.11, HTTP download of the 9598-byte POAP script
and matching embedded MD5. Options 3 (router) and 6 (DNS) were required; omitting
these caused offer rejection. Script execution started and failed immediately;
full M2a replay remains unqualified. Exact hardware/release and the failure cause
are not established. See the [lab examples](../examples/lab-192.168.0.95/README.md)
and [evidence](dev-server-192.168.0.95.md). This does not establish image installation,
which M2a does not implement.

## 2026-09-29 M2b upgrade implementation

Added opt-in image upgrade mode, authenticated streaming from a read-only host
folder, actual-file catalog generator, bounded bootflash download and checksum,
native no-reload install/checkpoint handling, target-version registration recovery,
and delayed durable post-reboot validation. Defaults remain planning-only. See
[m2b-upgrade-lab-guide.md](m2b-upgrade-lab-guide.md) for exact limitations and setup.
Earlier statements that image installation is unimplemented describe M2a history.
Physical upgrade qualification and Cisco matrix approval remain outstanding.

## Local YAML inventory

Added explicitly selected, read-only local YAML inventory for all three execution
modes, including live rereads for authorization and worker validation. NetBox is
still the default; local mode does not construct a NetBox client or require its
credentials. `compose.local.yaml` removes NetBox service secret references. See
[local-yaml-inventory.md](local-yaml-inventory.md). Earlier statements that NetBox
is mandatory describe the previous implementation. This adds structured inventory,
not serial-named raw configuration-file fallback.

## PoC v0.1 standalone stack

`compose.poc.yaml` provides HTTP-only delivery with local YAML or NetBox, no
certificate setup, no SSH key requirements and no validation worker. A minimal
password-based switch configuration is rendered. Manifests/status explicitly say
`validation_policy: manual`; CONFIG_STAGED is not treated as verified completion.
The standard stack retains its existing key requirements and SSH validation.
`scripts/prepare_poc.py` prepares a shared model/source catalog and HTTP bootstrap.
See [poc-v0.1.md](poc-v0.1.md). Hardware upgrade qualification is still pending.
