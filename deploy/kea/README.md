# Lite DHCP

`start.sh` starts Kea using the mounted configuration. The Linux Lite example is
`deploy/lite/kea.json`, selected with `ZTP_KEA_CONFIG`. Review its interface, subnet,
pool and options before enabling the Compose `dhcp` profile. No TFTP service is
included in this branch.
