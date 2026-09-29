# Cisco POAP reference

Reviewed upstream repository revision:
`9182181a48e7ba59cf5f85eb5998c86182931942` (retrieved 2026-09-06).

- [Pinned Cisco sample](https://github.com/datacenter/nexus9000/blob/9182181a48e7ba59cf5f85eb5998c86182931942/nx-os/poap/poap.py)
- [Cisco POAP lifecycle](https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/102x/configuration/fundamentals/cisco-nexus-9000-nx-os-fundamentals-configuration-guide-102x/m-using-poap.html)
- [VRF socket API](https://developer.cisco.com/docs/nx-os/vrf-module/)

This repository's bootstrap is an original, small configuration-only implementation;
it does not vendor or modify Cisco source or its license. The upstream sample
includes image install behavior even in some already-target branches, so it must
not be executed wholesale in M2a. The selected mechanism schedules a verified config
with `copy bootflash:<file> scheduled-config` and exits successfully. Native replay,
including any reload and startup save, must be demonstrated on each supported
factory/target release. No profile is hardware-qualified by default.

Python 3, `cli.cli`, `cisco.vrf.set_global_vrf`, inventory/version JSON schema and
TLS support are explicit qualification requirements. Legacy Python 2 is unsupported.
The embedded MD5 header is generated for native POAP compatibility, not authenticity.
No keys, passwords or device configs belong in the public bootstrap directory.
