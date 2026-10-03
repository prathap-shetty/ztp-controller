# ZTP Lite

A lightweight Linux controller for Cisco NX-OS power-on provisioning. It supports
NetBox, InfraHub or local YAML inventory, approved image delivery and installation,
configuration staging, explicit reprovisioning, and a token-protected dashboard
with registration failure diagnostics.

**This branch contains the Linux Lite deployment only.** It uses HTTP, switch
password authentication and manual post-boot verification. It disables NetBox TLS
verification and SSH host-key validation. A dashboard token does not encrypt
traffic. Run it on a controlled provisioning network; it is not a hardened
production deployment or a multivendor controller.

## Start here

1. Follow the [user guide](docs/ztp-lite-user-guide.md).
2. Select [local YAML](docs/local-yaml-inventory.md), NetBox (covered in the guide),
   or [InfraHub](docs/infrahub-inventory.md).
3. Read the [internal repository import guide](docs/internal-repository.md) before
   publishing to your internal Git server.

The only Compose entry point is `compose.lite.yaml`, using `.env.lite`.
Large firmware files, credentials and device inventory stay outside Git. No
firmware is bundled. Use licensed images and vendor-approved upgrade paths.

## Scope

- NX-OS configuration and single-image upgrade workflow; equal/newer installed
  numeric releases can skip installation under the configured policy.
- Explicit chassis serial/model authorization and SHA-256 image checks.
- DHCP through Kea on a dedicated Linux NIC, enabled with `--profile dhcp`.
- Manual verification: staging a config does not establish successful reboot or
  saved startup configuration.
- No automatic rollback, fleet scheduling, or automatic clearing of failed attempts.

The tested shared Python modules and regression tests remain in this branch,
including legacy internal names such as `poc_mode`. Alternate Mac PoC and standard
TLS/SSH deployment files are removed; shared runtime code has not been refactored.
Future multivendor work is [planned separately](docs/multivendor-implementation-plan.md).
