# ZTP Lite implementation status

This branch packages the working Linux NX-OS workflow with local YAML, NetBox and
InfraHub inventory; image/config delivery; equal/newer-version policy; explicit
reprovisioning; token-protected UI; and controller failure diagnostics.

Operator verification is required after configuration staging. Lab evidence is not
a blanket qualification of all hardware, releases or upgrade paths. The controller
has no automatic rollback or multivendor execution support.

The branch removes alternate deployment files and lab-specific records while
preserving shared Python internals and regression tests. This is a packaging
change, not the platform-adapter refactor. See the
[multivendor roadmap](multivendor-implementation-plan.md) for that future work.

See the [user guide](ztp-lite-user-guide.md) and
[internal import guide](internal-repository.md) for setup and migration.
