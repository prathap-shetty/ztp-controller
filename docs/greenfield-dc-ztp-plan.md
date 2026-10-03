# Greenfield Data Centre ZTP Plan

Implementation progress: [M1/M2a status and remaining work](implementation-status.md).

## Objective

Build a Zero Touch Provisioning service for a controlled greenfield
data-centre deployment. The first implementation focuses on **Cisco
Nexus / NX-OS using POAP**, while keeping clean abstractions so the
inventory can later move from NetBox to Infrahub and additional vendors
can be added without redesigning the controller.

Initial assumptions:

-   New Nexus switches are pre-created in NetBox with serial numbers.
-   Only devices with NetBox status `staged` are eligible.
-   Initial intent is available from NetBox device data and Config
    Context.
-   FastAPI is the ZTP control/orchestration API.
-   NX-OS POAP is the device-side bootstrap.
-   Deploy the ZTP services as one Docker Compose project on a Linux host.
-   Use ISC Kea DHCPv4 by default, with optional TFTP bootstrap fallback.
-   Existing NetBox and site DNS/NTP remain external dependencies.
-   Software upgrade is included when the factory image differs from the
    approved target.
-   Failed provisioning leaves the device `staged`.

## Architecture

``` text
Controlled ZTP VLAN/VRF
        |
   ISC Kea DHCPv4
        |
Factory-default Nexus
        |
   NX-OS POAP
        |
        v
+-------------------+
| FastAPI ZTP       |
| Controller        |
+---------+---------+
          |
   +------+------------------+
   |                         |
   v                         v
InventoryProvider       Artifact Service
   |                    POAP / images / configs
   v
NetBox
```

Responsibilities are deliberately separated:

-   **FastAPI** owns authorization, provisioning workflow, manifests,
    state and validation.
-   **InventoryProvider** owns source-of-truth access.
-   **VendorAdapter** owns platform-specific ZTP behavior.
-   **Artifact service** serves POAP files, approved NOS images and
    configuration artifacts.

> FastAPI owns the provisioning workflow. InventoryProvider owns
> source-of-truth access. VendorAdapter owns platform-specific ZTP
> behavior.

## Single-stack deployment and DHCP

Package the service as **one Docker Compose project with separate containers**, not one combined container. This is a single-host MVP; it does not provide high availability. NetBox remains the existing external source of truth.

| Service | Responsibility | Storage/network |
| --- | --- | --- |
| `kea-dhcp4` | ISC DHCP leases and release-qualified POAP boot options | Persistent lease volume; Linux host networking, bound to the dedicated ZTP interface |
| `ztp-api` | Registration, authorization, manifests and status | Private Compose network; exposed through nginx |
| `ztp-worker` | Durable validation and NetBox activation reconciliation | Private network with routed access to final switch management and NetBox |
| `postgres` | Provisioning attempts, events and durable jobs | Persistent volume; no public database port |
| `nginx` | API reverse proxy and HTTP(S) bootstrap/image/config delivery | Explicit host provisioning IP/ports; read-only artifact access |
| `tftp` (optional profile) | Read-only bootstrap fallback | Linux host networking; dedicated public-bootstrap directory only |

Use **ISC Kea**, the maintained DHCP product offered on the supplied ISC download page, as the proposed default. The separately named **ISC DHCP 4.x (`dhcpd`) is end-of-life**. If an existing deployment specifically requires dhcpd, document a mutually exclusive legacy override; never run both servers on the same scope. Kea JSON and dhcpd configuration syntax are different. Pin a supported Kea release and container build at implementation time. [ISC downloads](https://www.isc.org/download/)

For the first deployment, attach a dedicated Linux host interface/VLAN to the isolated ZTP network and bind Kea only there. A routed DHCP relay is an alternative deployment profile that requires separate validation. Ordinary Docker bridge port publishing does not establish DHCP broadcast connectivity. Docker Desktop on the development Mac is suitable for API tests, but is not the qualified physical-switch DHCP host. Linux host networking shares the host network namespace and does not use published-port mappings. [Docker host networking](https://docs.docker.com/engine/network/drivers/host/)

Keep Kea leases on a persistent memfile volume, separate from controller PostgreSQL. Configure subnet, pool, excluded/reserved infrastructure and final static management addresses, router, DNS, lease timers and boot options in versioned deployment configuration. A lease does not authorize provisioning; NetBox checks still happen at registration. Kea supports interface selection, persistent memfile leases and configuration validation. [Kea DHCPv4 documentation](https://kea.readthedocs.io/en/stable/arm/dhcp4-srv.html)

DHCP advertises addresses reachable by switches, never Docker service names. The worker must have a route to the final management network after the DHCP-to-static transition. Supply database credentials, NetBox token, validation credentials and TLS keys through mounted secrets. Persist generated artifacts and workflow data; mount only the required artifact directories read-only into serving containers. Keep the DHCP service independent of API/database startup, while provisioning clients retry unavailable APIs within a bounded budget.

## POAP transports and optional TFTP fallback

Treat three channels independently: **initial script retrieval**, **controller API**, and **image/config downloads**. Cisco documents both TFTP bootstrap infrastructure and script transfer options including HTTP(S) and TFTP; support must still be qualified against the factory release. [Cisco DevNet POAP overview](https://developer.cisco.com/docs/nx-os/poap/#poap-poweron-auto-provisioning)

| Channel | Preferred path | Fallback policy |
| --- | --- | --- |
| Bootstrap script | Secure POAP HTTPS when qualified; explicit legacy HTTP where necessary | Optional TFTP mode on the isolated ZTP network |
| Registration/status/manifest | HTTPS API, using qualified on-box trust and VRF support | TFTP cannot replace an HTTP API; a legacy HTTP API profile requires explicit site policy |
| NX-OS images | HTTP(S) artifact service with trusted SHA-256 verification | TFTP image delivery deferred pending large-file/client qualification |
| Per-device configuration | Authorized HTTP(S) delivery | No configuration or secrets in the public TFTP root |

Provide named DHCP transport profiles: `secure-https`, `legacy-http`, and `legacy-tftp`. Configure boot information per qualified client class/reservation or lab scope, not as a universal option-67 URL. Secure POAP IPv4 option 43 and legacy options 66/67 have release-specific behavior. Cisco permits legacy fallback options alongside secure options, but that does not prove a particular failure will trigger an HTTPS → HTTP → TFTP sequence. Test actual factory firmware and record the conditions before enabling automatic fallback. [Cisco release-specific POAP guide](https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/102x/configuration/fundamentals/cisco-nexus-9000-nx-os-fundamentals-configuration-guide-102x/m-using-poap.html)

The reliable MVP fallback is operator-selected `legacy-tftp`: enable the optional TFTP service, select the tested DHCP boot profile and retry POAP using the documented recovery procedure. Merely starting a TFTP container does not redirect an already-failing HTTPS download. Before the bootstrap is downloaded, Python code cannot implement fallback for its own retrieval.

Serve only a pinned, non-secret bootstrap script and compatibility checksum files through TFTP, using a read-only root with uploads disabled. TFTP has no per-attempt API token enforcement; its public files are an explicit exception to private artifact grants. Scope access to the ZTP interface/subnet. Permit UDP 69 plus the selected daemon's configured transfer-port range in host/network firewalls; publishing UDP 69 alone is insufficient for a complete TFTP transfer. Record and verify the range and NX-OS block-size/retry interoperability before deployment.

Fallback is disabled unless selected by policy; do not silently downgrade on certificate-validation failure. Log selected mode, reason and result. Retain image/config SHA-256 checks, but do not treat a checksum delivered with an unauthenticated script as proof of authenticity. API unavailability or NetBox denial stops provisioning even when TFTP retrieval succeeds.

Use the [Cisco POAP source directory](https://github.com/datacenter/nexus9000/tree/master/nx-os/poap) as an upstream reference. Pin a reviewed commit and preserve licensing; adapt the native installation/replay logic rather than fetching `master` at runtime. Maintain the required script checksum after modifications and record supported interpreter/transfer modules.

## Generic Inventory Model

Do not allow NetBox-specific structures to leak into ZTP business logic.

``` python
from typing import Any
from pydantic import BaseModel, Field

class DeviceIdentity(BaseModel):
    id: str
    name: str
    serial_number: str
    vendor: str
    platform: str
    model: str | None = None
    status: str

class ManagementAddress(BaseModel):
    address: str | None = None
    gateway: str | None = None
    vrf: str | None = None

class SoftwareIntent(BaseModel):
    target_version: str | None = None
    image_name: str | None = None
    image_checksum: str | None = None

class DeviceIntent(BaseModel):
    device: DeviceIdentity
    management: ManagementAddress | None = None
    software: SoftwareIntent | None = None
    config_context: dict[str, Any] = Field(default_factory=dict)
```

NetBox is translated into `DeviceIntent`. Later, an
`InfrahubInventoryProvider` translates Infrahub objects into the same
model.

## Inventory Provider

``` python
from abc import ABC, abstractmethod

class InventoryProvider(ABC):
    @abstractmethod
    def get_device_by_serial(self, serial: str):
        ...

    @abstractmethod
    def get_device_intent(self, device_id: str):
        ...

    @abstractmethod
    def update_device_status(self, device_id: str, status: str):
        ...

    @abstractmethod
    def get_management_ip(self, device_id: str):
        ...
```

Implement `NetBoxInventoryProvider` first and
`InfrahubInventoryProvider` later. Provider selection is server-side
configuration; a device must never be able to choose the inventory
backend.

## NetBox Eligibility Policy

Minimum ZTP authorization:

``` text
serial exists in NetBox
AND status == staged
AND vendor/platform match
AND model matches where available
AND ZTP policy permits provisioning
```

Recommended Config Context/custom-field policy:

``` yaml
provisioning:
  ztp_enabled: true

ztp:
  target_nxos: "APPROVED_VERSION"
  image:
    filename: "APPROVED_NXOS_IMAGE.bin"
    sha256: "EXPECTED_SHA256"
```

`staged` is the lifecycle status; detailed provisioning progress is
tracked separately.

## NX-OS POAP Workflow

``` text
Factory Nexus
  -> DHCP
  -> POAP bootstrap
  -> discover serial/model/current NX-OS
  -> POST /api/v1/ztp/register
  -> NetBox lookup through InventoryProvider
  -> verify staged + ZTP policy + expected platform/model
  -> return manifest
  -> download/verify initial configuration and approved image if required
  -> stage native POAP configuration replay
  -> install approved NX-OS if required
  -> reload/replay using qualified POAP lifecycle
  -> controller independently validates final management state
  -> NetBox staged -> active with durable reconciliation
  -> mark provisioning COMPLETE
```

The workflow must be **idempotent** so a software reload cannot cause an
upgrade loop.

## Provisioning State

Track separately from NetBox device status:

``` text
DISCOVERED
 -> AUTHORIZED
 -> IMAGE_DOWNLOADING
 -> IMAGE_VERIFIED
 -> INSTALLING
 -> REBOOTING
 -> CONFIGURING
 -> VALIDATING
 -> VALIDATED
 -> ACTIVATION_PENDING
 -> COMPLETE
```

Any pre-activation provisioning stage can transition to `FAILED`. Inventory
synchronization failures remain `ACTIVATION_PENDING` for reconciliation; they
must not cause another switch installation. Already-target devices skip image
installation. Persist attempts and worker jobs in PostgreSQL. Native replay does
not imply the bootstrap Python script runs again after reboot.

The device remains `staged` throughout provisioning. Only successful
final validation changes it to `active`. Failure leaves it staged and
records the failure reason.

## FastAPI API

Suggested initial endpoints:

``` text
GET  /health
POST /api/v1/ztp/register
GET  /api/v1/ztp/config/{authorized-reference}
POST /api/v1/ztp/status/{provisioning_id}
GET  /api/v1/ztp/status/{provisioning_id}
```

Observed registration facts:

``` json
{
  "serial_number": "FDO12345678",
  "vendor": "cisco",
  "platform": "nxos",
  "model": "N9K-C93180YC-FX3",
  "current_version": "CURRENT_VERSION",
  "management_mac": "00:11:22:33:44:55"
}
```

The controller returns desired state; it never exposes source-of-truth
credentials.

## Configuration Generation

Prefer:

``` text
NetBox device data
       +
NetBox Config Context
       +
Jinja2 NX-OS template
       |
       v
Generated initial NX-OS configuration
```

Keep intent structured where practical instead of storing one large CLI
blob in Config Context.

## Greenfield Security Model

The controlled site reduces exposure, but build the correct boundaries
from the beginning.

### Isolated provisioning network

Put factory-default switches in a dedicated ZTP VLAN/VRF. Permit only
required DHCP/bootstrap, FastAPI, artifact server, and necessary DNS/NTP
access. Do not give unprovisioned devices broad production-management
access.

### Serial is identity, not authentication

Use serial number for inventory lookup, not as cryptographic proof. For
this controlled greenfield MVP, serial + staged status + expected
model/platform + controlled network is a pragmatic starting point.
Preserve the ability to add stronger authentication later.

### Explicit authorization

Prefer:

``` text
serial exists
AND status == staged
AND ztp_enabled == true
AND vendor/platform/model match
```

### Credentials and secrets

The switch never receives NetBox/Infrahub API credentials. FastAPI uses
least-privilege credentials. Avoid reusable production secrets in Config
Context; later integrate Vault or another secrets manager.

### Image integrity

Maintain an approved software catalog with platform/model family,
version, filename and SHA-256. Verify software before installation.

### Artifact access

Do not trust client-supplied filesystem paths or arbitrary filenames.
Mature deployments should use short-lived configuration/artifact tokens
or signed URLs.

### Secure transport

Use TLS/Secure POAP capabilities where supported. Test against the
**oldest factory NX-OS version expected in the new DC**, not only the
target version.

## Artifact Service

Use FastAPI for the control plane:

``` text
authorization
manifest generation
inventory interaction
state/audit
validation
```

Use nginx or an artifact/object repository for bulk files:

``` text
POAP bootstrap
NX-OS images
generated config artifacts
```

Avoid streaming multi-gigabyte NX-OS images through FastAPI without a
specific reason.

## Future Multivendor Support

Build NX-OS first but preserve a vendor interface:

``` python
class ZtpVendorAdapter:
    def build_manifest(self, intent, observed_state):
        ...

    def validate_device(self, intent, observed_state):
        ...

    def bootstrap_type(self):
        ...
```

First: `CiscoNxosAdapter`.

Future possibilities: `AristaEosAdapter`, `JuniperJunosAdapter`,
`NokiaSrLinuxAdapter`.

Vendor-specific image installation and reload semantics stay inside
adapters.

## Suggested Repository

``` text
ztp-controller/
├── app/
│   ├── main.py
│   ├── settings.py
│   ├── models/
│   │   ├── device.py
│   │   ├── intent.py
│   │   ├── manifest.py
│   │   └── provisioning.py
│   ├── inventory/
│   │   ├── base.py
│   │   ├── factory.py
│   │   ├── netbox.py
│   │   └── infrahub.py
│   ├── vendors/
│   │   ├── base.py
│   │   └── cisco_nxos.py
│   ├── services/
│   │   ├── authorization.py
│   │   ├── provisioning.py
│   │   ├── rendering.py
│   │   └── validation.py
│   └── api/
│       └── ztp.py
├── templates/cisco/nxos_initial.j2
├── poap/cisco/poap.py
├── tests/
│   ├── unit/
│   └── integration/
├── requirements.txt
├── README.md
└── .env.example
```

## Greenfield MVP

Implement now:

1.  FastAPI service.
2.  NetBox provider.
3.  Nexus lookup by serial.
4.  Reject unknown/non-staged devices.
5.  Required explicit `provisioning.ztp_enabled: true` authorization.
6.  Retrieve Config Context.
7.  Normalize into `DeviceIntent`.
8.  Cisco NX-OS adapter.
9.  Generate deterministic provisioning manifest.
10. Generate initial config from NetBox + Config Context.
11. POAP bootstrap.
12. Approved NX-OS image selection.
13. SHA-256 verification.
14. Upgrade when required.
15. Safe resume after reload.
16. Apply initial config.
17. Track provisioning state.
18. Post-provision validation.
19. Change `staged -> active` only after success.
20. Record failure and leave device staged otherwise.
21. One Linux Docker Compose project including ISC Kea DHCPv4.
22. Optional read-only TFTP bootstrap profile with explicit fallback policy.

Defer initially:

-   Actual Infrahub implementation (keep interface/placeholder).
-   Other vendor adapters.
-   Advanced UI.
-   Large workflow engine.
-   Multi-site policy complexity.
-   Production PKI complexity beyond what is required for the controlled
    site.

## Test Sequence

1.  Test NetBox serial lookup.
2.  Test staged authorization.
3.  Reject unknown serial.
4.  Reject non-staged device.
5.  Test Config Context normalization.
6.  Test generated NX-OS config.
7.  Test manifest generation.
8.  Test image checksum validation.
9.  Test DHCP/POAP with one Nexus.
10. Test a Nexus already at target version.
11. Test one requiring upgrade.
12. Test resume after upgrade/reload.
13. Deliberately fail image/config and confirm device stays staged.
14. Run post-provision validation.
15. Confirm NetBox becomes active only after success.
16. Repeat with multiple switches.

## Definition of Done

A Nexus can be installed and powered on with no manual switch
configuration, and the system:

``` text
identifies device
-> authorizes against NetBox
-> verifies staged
-> selects approved NX-OS
-> upgrades if necessary
-> verifies software
-> obtains intended configuration
-> configures switch
-> validates result
-> records outcome
-> changes NetBox device to active
```

Manual intervention is reserved for exception/failure cases.

## Codex Handoff Prompt

> Read this entire design document before making changes. Build the
> greenfield MVP incrementally. The first supported platform is Cisco
> Nexus NX-OS using POAP and the first inventory provider is NetBox.
> Preserve the InventoryProvider and VendorAdapter abstractions so
> Infrahub and other network vendors can be added later. NetBox is the
> current source of truth: devices are pre-created with serial numbers,
> only devices in staged status are eligible for ZTP, and initial intent
> is supplied through device data and Config Context. Do not create a
> second inventory database. Implement tests for authorization and
> provider normalization before implementing device-side POAP behavior.
> Treat serial number as an inventory identity rather than strong
> authentication. Keep NX-OS image/config artifact serving separate from
> source-of-truth access. A device must remain staged on failure and may
> become active only after successful post-provision validation. Start
> with the smallest working end-to-end implementation suitable for a
> controlled greenfield DC.

## First Codex Milestone

Stop before performing a real software upgrade. First implement and
test:

``` text
POAP/test registration
 -> FastAPI /register
 -> NetBox lookup by serial
 -> verify staged
 -> read Config Context
 -> normalize DeviceIntent
 -> CiscoNxosAdapter
 -> deterministic manifest
```

Once this is reliable, milestone two adds real POAP image download,
verification, installation, reload/resume and final configuration.

This staged implementation avoids debugging DHCP, NetBox, POAP, software
upgrades, config generation and reload recovery all at once.

## Deployment acceptance additions

- Validate Kea configuration and capture DHCP offers for each supported boot profile on the real ZTP interface/relay.
- Prove primary bootstrap retrieval and an operator-selected TFTP fallback on qualified hardware; claim automatic fallback only after an observed firmware test.
- Test TFTP disabled, missing script, checksum mismatch, firewall transfer-port restrictions, and retries without opening access to config secrets.
- Prove successful TFTP retrieval cannot bypass denied registration or an unavailable API.
- Restart the whole Compose project and verify lease, attempt, artifact and worker-job persistence without an upgrade loop.
- Verify the DHCP service does not answer on unintended interfaces and the worker can validate the final static management address.

For detailed milestones and implementation gates, see [Cisco NX-OS POAP implementation plan](cisco-nxos-poap-implementation-plan.md).
