# Approved planning profiles

The default catalog is empty and denies all devices. Copy the structure from
`tests/fixtures/profiles.json` into a deployment-owned file, replace every lab
value with approved software metadata, and set `ZTP_CATALOG_PATH`.

Version matching is exact after surrounding input validation: no lexical version
ordering or inferred upgrade path. Include the target release in `source_versions`
to support already-target devices. Model and image checksum must agree with NetBox.
Profiles authorize planning only. Even `hardware_qualified: true` cannot enable
execution in M1. The fixture checksum is deliberately fake and is not an approved
Cisco image. No binaries belong in this repository.
