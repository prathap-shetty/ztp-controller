# M2b: native POAP image upgrade lab

Implemented for qualification, **not yet tested on physical hardware**. Requested
path: N9K-C93180YC-FX3, 10.4(4) → 10.5(4). Cisco names the target 10.5(4)M and lists
`nxos64-cs.10.5.4.M.bin` for this image family. Use the exact release strings from
`show version` in the profile and NetBox; the generator defaults to 10.4(4)M →
10.5(4)M. No firmware binary, digest or file size is fabricated or bundled.

This is a Day-0 POAP workflow on an unconfigured lab switch. It is not an in-service
upgrade orchestrator for a configured production switch, an ISSU guarantee, an
EPLD upgrade manager, or an automatic multi-hop/rollback engine.

## Workflow

1. Discover identity and source release; authorize against NetBox and an explicitly
   approved profile. Render and persist the immutable target configuration.
2. Download the profile-selected image over the authenticated HTTPS API. The API
   streams from a read-only host folder with bounded memory. No public image URL,
   directory listing, arbitrary filename request, redirect or TFTP image downgrade.
3. Check bootflash free space (image plus 256 MiB reserve), stream to a partial file,
   verify exact size and SHA-256, fsync, then rename. An existing matching image is
   reused; interrupted transfers restart from byte zero, up to three transport tries.
   Existing nonmatching images and unrelated bootflash files are not deleted.
4. Record IMAGE_VERIFIED on the controller after rechecking live authorization.
   Persist an `installing` bootflash checkpoint and invoke:
   `terminal dont-ask ; install all nxos bootflash:<digest-named-image> no-reload non-interruptive`.
   This follows the no-reload command form in Cisco's POAP sample; its behavior on
   this exact hardware/release remains a physical acceptance requirement. It is not
   a promise of non-disruptive operation. No override, direct reload, or write erase
   is issued by this implementation.
5. Persist `image-installed`, schedule the configuration with `copy ... scheduled-config`,
   record `staged`, and exit successfully. Native POAP owns reboot, target-image
   configuration replay, and startup save. The controller worker survives restart
   and checks the final version, identity, running configuration and startup configuration.
6. If the bootstrap is invoked again at the target version, registration retains
   the original immutable attempt and validation deadline. Only the approved source
   or exact target is eligible; identity, intent, profile and config must still match.
   A checkpoint interrupted inside installation or scheduling requires operator
   recovery; it does not blindly repeat a potentially destructive command. A target
   boot can continue an installation checkpoint without reinstalling.

The controller retains existing AUTHORIZED/CONFIGURING/VALIDATING/VALIDATED/FAILED
states; download/installation details are recorded in the device checkpoint and
IMAGE_VERIFIED event. It does not mark NetBox active. An unavailable switch times
out; there is no automatic downgrade or rollback. Native rollback behavior must
be inspected through the console following a failed install.

## Host image folder and profile

Obtain the licensed image and independently verified SHA-256 from Cisco. Keep image
files immutable during provisioning; finish copying before publishing the catalog.
On the Linux server:

```sh
mkdir -p /srv/ztp/images
# Place your Cisco-provided nxos64-cs.10.5.4.M.bin in that folder.
python3 scripts/catalog_image.py /srv/ztp/images/nxos64-cs.10.5.4.M.bin \
  --approved-sha256 YOUR_CISCO_SHA256 \
  --output catalog/site-upgrade.json
```

The generator streams the checksum, records actual byte size, refuses to overwrite
an existing catalog and leaves `upgrade_path_approved=false`. Check Cisco's matrix
for the exact source/target/platform, and review applicable release restrictions
before changing that flag to true. The interactive matrix's exact result was not
verified during implementation; support for this direct path is not asserted.
Keep `hardware_qualified=false` during lab qualification. Match the generated
filename/digest/target in NetBox. Keep the exact hardware serial, staged status,
mgmt0 primary IP and other M2a context requirements.

Set in the server `.env`:

```dotenv
ZTP_IMAGE_DIR=/srv/ztp/images
ZTP_CATALOG_FILE=./catalog/site-upgrade.json
ZTP_EXECUTION_MODE=upgrade-and-configure
ZTP_ALLOW_UNQUALIFIED_LAB=true
ZTP_UPGRADE_VALIDATION_DELAY_SECONDS=1800
ZTP_UPGRADE_DEADLINE_SECONDS=7200
ZTP_VALIDATION_RETRY_SECONDS=60
ZTP_VALIDATION_MAX_ATTEMPTS=100
```

Configure NetBox credentials, validation SSH keys/known_hosts, DHCP, TLS and the
bootstrap per [M2a](m2a-lab-guide.md). Ensure nginx/API UID 10001 can read image files
(the image mount is on the API, not nginx); only the worker mounts the SSH private
key. Large binaries are excluded from Git and Docker build contexts.

Re-release `poap.py` with the site HTTPS origin and public CA after this update,
then rebuild/recreate the stack. Default execution remains planning-only. The
image endpoint is `/api/v1/ztp/image/<attempt-id>` with the existing bearer grant;
expired grants or changed/revoked inventory deny access. No range/resume protocol
is implemented. Default grant lifetime is one hour; download tries are bounded to
30-minute transfer windows. Tune budgets for your image size and lab speed before
running; a retry cannot extend the original validation deadline.

## Acceptance and recovery

Capture serial/PID/source release and Cisco upgrade-matrix result first. Verify
Python 3/CLI/VRF compatibility, image size/hash and bootflash capacity. Keep console
access throughout this disruptive lab run. Confirm image download, installation
success, native reboot into the target, scheduled replay, startup save, and final
VALIDATED evidence. Test bad hashes, expired/revoked authorization, interrupted
transfer, controller restart, failed install, power loss and target-version retry.
Never remove bootflash checkpoints or database history to force a repeat without
reconciling the actual install/replay state through the console.

New profile fields change serialized plan hashes; existing in-flight M2a records
may report attempt conflicts. Reconcile existing lab attempts before switching
modes. Do not deploy this change midway through a provisioning attempt.

## Sources

- [10.5(4)M release notes and image family](https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/105x/release-notes/cisco-nexus-9000-nxos-release-notes-1054M.html)
- [Cisco upgrade/ISSU matrix](https://www.cisco.com/c/dam/en/us/td/docs/dcn/tools/nexus-9k3k-issu-matrix/index.html)
- [10.4 POAP native reboot and replay lifecycle](https://www.cisco.com/c/en/us/td/docs/dcn/nx-os/nexus9000/104x/configuration/fundamentals/cisco-nexus-9000-series-nx-os-fundamentals-configuration-guide-release-104x/m-using-poap.html)
- [Cisco POAP reference install_nxos_issu](https://github.com/datacenter/nexus9000/blob/master/nx-os/poap/poap.py)

The Cisco sample also contains destructive operations not adopted here. This is an
original implementation with a narrower lifecycle; source review is not hardware
qualification. It requires the full native lifecycle test above.
