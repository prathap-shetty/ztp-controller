# Lite image catalog

Generate `catalog/lite.json` with `scripts/prepare_lite.py` after verifying the
licensed firmware checksum and model/upgrade-path compatibility. Point
`ZTP_CATALOG_FILE` at that file. The committed `profiles.json` is empty and denies
all devices; example/test profiles contain placeholders, not approved metadata.

A profile must match the inventory's chassis model, image filename, SHA-256 and
target release. Source-release matching and equal/newer-version behavior follow
the explicit Lite policy settings described in the user guide. Do not assume
numeric ordering proves an upgrade path is supported by Cisco.

Keep generated deployment catalogs and firmware out of Git. Images are mounted
read-only from the host into the API container.
