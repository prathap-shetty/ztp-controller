# Lite DHCP

`start.sh` starts Kea using the mounted configuration. The Linux Lite example is
`deploy/lite/kea.json`, selected with `ZTP_KEA_CONFIG`. Review its interface, subnet,
pool and options before enabling the Compose `dhcp` profile. No TFTP service is
included in this branch. For two management VLANs, start from
`examples/multi-vlan/kea.json`; see the user guide. Per-platform single-scope
examples are in `examples/nxos/`, `examples/iosxr/`, `examples/iosxe/` and `examples/eos/`;
`examples/nxos/kea.json` matches this default. `examples/mixed-platform/kea.json`
selects the script per platform on one subnet with client classes.

All shipped configurations set `"cache-threshold": 0.0`. NX-OS POAP rejects a lease
shorter than one hour (`Lease time acuired (%d) is less than 1 Hr`), and Kea's
default lease cache re-offers an existing lease with only its remaining lifetime.
Keep caching disabled and `valid-lifetime` at 3600 seconds or more in custom files.
