# Multivendor controller implementation plan

Status: proposed future work. No runtime refactor or additional platform support
is included in this document. The current executable workflow remains Cisco
NX-OS POAP; NetBox, InfraHub and local YAML are inventory sources, not separate
network-platform implementations.

## Problem and objective

`app/services/rendering.py::render_configuration()` selects NX-OS templates and
configuration semantics directly. The existing `ZtpVendorAdapter` exposes manifest
construction and bootstrap type, but does not isolate all platform behavior.
Version comparisons, management interface/VRF rules, native installation commands,
configuration replay and SSH validation also contain NX-OS assumptions.

The objective is a controller that can add a supported platform through an explicit
adapter and its artifacts, while keeping inventory integration, authorization,
attempt persistence and operator visibility shared. Preserve the currently tested
NX-OS behavior throughout the transition. A generic rendering function alone is
not sufficient to claim multivendor support.

## Proposed boundaries

| Component | Responsibility |
| --- | --- |
| Inventory providers | Read source records and normalize identity and requested intent; preserve explicit platform-specific inputs without guessing |
| Shared orchestration | Resolve adapter, authorize identity and intent, persist plans/events, issue tokens, serve artifacts and schedule validation |
| Platform registry | Resolve a registered adapter by canonical `(vendor, platform)`; reject unknown or conflicting identities |
| Platform adapter | Validate platform intent, select compatibility profiles, compare releases, render configuration, build the action plan and validate observed results |
| Device bootstrap | Implement the platform's provisioning protocol and execute only its supported actions; report common events and sanitized diagnostics |
| Deployment/network services | Serve bootstrap artifacts and select DHCP options according to the platform and supported device-identification rules |
| Dashboard | Display shared lifecycle states and platform-specific progress details without assuming every device uses POAP |

Illustrative API direction (not a final interface):

```python
adapter = registry.resolve(intent.device.vendor, intent.device.platform)
adapter.validate_intent(intent, observed)
plan = adapter.build_manifest(intent, observed, policy)
artifact = adapter.render_configuration(intent, policy)
```

Adapter capabilities should declare supported operations, such as configuration
only, image upgrade, post-boot validation and explicit reprovisioning. Do not
require every platform to support every operation. Avoid a shared orchestrator
with growing `if vendor == ...` branches or executable arbitrary command strings
provided by inventory records.

## Areas to generalize

1. **Contracts and schemas.** Separate common identity, addressing and lifecycle
   fields from typed platform intent. Remove global `mgmt0`, `management` VRF,
   `.bin` filename and NX-OS action assumptions where they are genuinely
   platform-specific. Keep validation strict and version schemas explicitly.
2. **Rendering.** Move NX-OS template selection, admin/key requirements and syntax
   validation behind its adapter. Preserve output and hashes for existing inputs.
3. **Compatibility and upgrades.** Delegate release ordering, image families,
   hardware matching, upgrade-path rules and already-at/newer-target behavior.
   Never reuse NX-OS version comparisons or install flags for another platform.
4. **Bootstrap and DHCP.** Define per-platform bootstrap delivery, runtime and
   native completion/reboot semantics. Specify how a mixed fleet receives the
   appropriate script using validated DHCP identity information or separate
   scopes. Do not assume every platform accepts NX-OS DHCP options or Python.
5. **Validation.** Resolve command sets, output parsers and configuration checks
   through the adapter. Retain shared retry, deadline and evidence handling.
6. **Inventory mappings.** Move NX-OS-specific authorization and interface rules
   out of provider-wide logic. Document equivalent fields for NetBox, InfraHub and
   YAML. Distinguish source-system platform labels from canonical adapter keys.
7. **State and compatibility.** Persist adapter identity, adapter revision and
   manifest schema version. Existing attempts must either remain executable by
   their original adapter revision or stop for explicit reconciliation. Never
   silently reinterpret an in-flight upgrade after a controller update.
8. **Diagnostics and UI.** Keep common error codes/stages, with sanitized
   adapter-specific details. Show vendor/platform and actual capabilities; do not
   imply verified completion when only configuration staging was acknowledged.

## Phased delivery

### MV1 — Inventory the platform assumptions and define contracts

- Map NX-OS dependencies across models, inventory providers, rendering, adapter,
  bootstrap, image handling, validation, reprovisioning and Compose/DHCP examples.
- Agree on the registry key, capability model, manifest versioning and typed
  platform extensions. Choose the second platform only when its hardware and
  provisioning protocol are available for qualification.
- Record the current NX-OS rendered output, action ordering and lifecycle as
  regression fixtures, including equal/newer-target and erase/reprovision cases.

Acceptance: reviewed interface and migration design with explicit unsupported
capability behavior; no production behavior change.

### MV2 — Put the existing NX-OS workflow behind the adapter

- Implement registry resolution and delegate rendering, version policy,
  platform-intent checks and validation to the NX-OS adapter.
- Preserve API defaults and existing deployments through a documented compatibility
  path. Migrate persisted contracts only where necessary, with backups and rollback.
- Keep inventory providers independent of adapter implementation details.

Acceptance: existing tests and lab flows pass; configuration artifacts and action
ordering remain equivalent; unsupported platforms fail before artifact execution;
old-attempt compatibility or reconciliation behavior is tested.

### MV3 — Add one second platform end to end

- Implement its typed intent, templates, compatibility catalog, bootstrap and
  validation provider, with a working DHCP/delivery example.
- Start with configuration-only provisioning; expose image upgrade only after its
  native workflow and interruption/recovery cases have been qualified.
- Prove that adding the adapter requires no platform branches in shared lifecycle
  orchestration. Document any genuinely shared contract additions.

Acceptance: real or representative supported hardware completes provisioning,
reports useful failures, and supports documented recovery. Mark support per model,
release and capability rather than labelling an entire vendor supported.

### MV4 — Mixed-fleet qualification and documentation

- Run different platforms concurrently, verify correct bootstrap selection and
  artifact isolation, and test identity mismatch and unsupported capabilities.
- Publish a support matrix, platform setup guides, source-of-truth mappings and
  upgrade/recovery constraints. Include migration instructions for NX-OS users.

Acceptance: independently reproducible mixed-fleet lab results and documented
operational limits, including security policy differences between Lite and the
certificate/key-validated deployment.

## Required verification

Use shared adapter-contract tests plus platform-specific behavior tests. Cover
unknown vendor/platform, contradictory discovered/inventory identities, duplicate
catalog matches, unsupported release ordering, wrong image family, image integrity,
rendering correctness, authorization revocation, interrupted upgrades, replay
idempotency and post-boot evidence. Test inventory sources independently of target
platforms and confirm that tokens/configuration never leak into diagnostics.

Unit tests and simulated bootstrap execution do not establish hardware support.
Retain the existing NX-OS lab qualification and qualify the second platform's
native boot/replay behavior before enabling its upgrade capability.

## Scope and decisions still open

- Second platform, initial models/releases and representative lab hardware.
- Minimum shared manifest/actions versus adapter-owned payload details.
- Versioned adapter packaging and support window for persisted attempts.
- Mixed-fleet DHCP identification and handling devices with ambiguous identifiers.
- Whether later platforms need additional configuration artifacts or delivery
  protocols beyond the current image/config endpoints.

Keep this work separate from adding another inventory source. No new platform,
platform-specific command syntax or upgrade path is promised until qualified.
