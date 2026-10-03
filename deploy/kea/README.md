# Kea boot profiles

These examples use Linux `eth1`, ZTP subnet `192.0.2.0/24`, host/artifact server
`192.0.2.2`, gateway `192.0.2.1`, and temporary pool `.100-.199`. Final management
addresses `.10-.99` are outside the dynamic pool. Replace these documentation
addresses before use. Kea is explicitly enabled by the `provisioning` profile.

Select one complete configuration using `ZTP_KEA_CONFIG`. `kea-dhcp4.json` defaults
to legacy HTTP bootstrap. `profiles/legacy-tftp.json` enables TFTP bootstrap in
DHCP; also enable the Compose `tftp` service profile. TFTP does not replace the API.
The secure profile sends Cisco option 43 encoded as a binary payload because its
sub-option lengths use two bytes; ordinary Kea one-byte suboption encapsulation
must not be substituted. Regenerate it with `scripts/kea_profiles.py` after editing
the address/URL parameters. No automatic downgrade is enabled in these examples.

Validate with `kea-dhcp4 -t`, then capture actual DHCP offers and prove the selected
boot mode on the switch. The image is ISC's amd64 build; this example targets a
Linux amd64 host. Docker Desktop emulation checks syntax but not physical L2.

Pinned ISC image reports Kea 3.2.0. The immutable digest in Compose was
resolved from ISC's available image because a short `3.0.4` image tag did not exist.

The image executable is owned by `kea:kea` with mode 0754. Our root process needs
`DAC_OVERRIDE` to execute it and access the lease directory with capabilities
dropped; `NET_RAW` and `NET_BIND_SERVICE` cover DHCP sockets. `/run/kea` is created
inside tmpfs by the entrypoint. No privileged container is used.

An explicit global option-43 binary definition (`encapsulate: ""`) is essential:
without it, Kea's default suboption handling rewrites the Cisco byte sequence even
though `kea-dhcp4 -t` passes. The supplied wire probe caught this and verifies the
actual emitted bytes. Native switch trust and direct broadcast behavior still need
hardware qualification.
