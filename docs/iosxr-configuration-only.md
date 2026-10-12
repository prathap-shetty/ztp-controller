# IOS XR configuration-only lab support

Status: opt-in implementation with simulated/bootstrap and controller tests.
**XRv9000 is the initial lab target, not yet hardware/runtime qualified.** No XR
image download, release change or software installation is implemented. The router
keeps its installed release. Existing NX-OS behavior remains separate.

## Prerequisites and limits

- XRv9000 must provide Python 3 at `/usr/bin/python3` and `ZtpHelpers` in `/pkg/bin`.
  Confirm these on your actual XR release before testing. The example release
  used in test fixtures is 24.2.2; this is not a claim that your router runs it.
- Initial support is IPv4 in the default VRF with one management interface, such
  as `MgmtEth0/RP0/CPU0/0`. Non-default VRFs and multiple management ports are not
  qualified. The bootstrap enters the `global-vrf` network namespace for HTTP.
- Discovery requires a chassis inventory record named `Rack 0`, `Chassis` or `0`
  with PID/SN, an XR version, and an unambiguous full management-interface name
  with a dotted MAC address. Unknown output formats fail rather than guessing.
- The interface name and PID in inventory must match the router. Virtual serial
  numbers may change after recreating the VM; update inventory accordingly.
- This is the existing Lite HTTP/password/manual-validation deployment. No new
  Apache service, certificate setup, image catalog entries or firmware is needed
  for XR. Keep the controller's catalog file present (`[]` is valid for XR-only).

## 1. Configure inventory

### Local YAML

Copy `inventory/devices.iosxr.example.yaml` to your ignored local
`inventory/devices.yaml` (or merge its device into your existing list). Replace
serial, model, hostname and management address; then set `ztp_enabled: true`.
Use the exact chassis PID from `show inventory`, not a marketing model name.

```yaml
schema_version: 1
devices:
  - device:
      id: lab-xrv9000-01
      name: lab-xrv9000-01
      serial_number: REPLACE_WITH_CHASSIS_SERIAL
      vendor: cisco
      platform: iosxr
      model: IOSXRV9000
      status: staged
    ztp_enabled: true
    management:
      address: 10.10.10.100/24
      gateway: 10.10.10.1
      interface: MgmtEth0/RP0/CPU0/0
      vrf: default
```

Omit `software` entirely. IOS XR rejects software intent. The initial XR template
sets hostname, admin password, SSH, management address and default route. It does
not render arbitrary configuration-context data; customize
`templates/cisco/iosxr_lite.j2` for additional configuration and rebuild the API.

### NetBox

Set platform slug `cisco_iosxr`, manufacturer slug `cisco`, status `staged`, and
the real serial/PID. Assign the primary IPv4 address to the device's actual
management interface. Use this config context, without NX-OS image/version fields:

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "management": {
      "interface": "MgmtEth0/RP0/CPU0/0",
      "vrf": "default",
      "gateway": "10.10.10.1"
    }
  }
}
```

### InfraHub

Use the existing ZTP schema extension. Set platform name `cisco_iosxr`, Cisco
manufacturer, staged status, serial and exact part number; set `mgmt_interface`
to the real interface and assign the primary address to that same device/interface.
Set `ztp_enabled=true` and `ztp_gateway`. Leave `ztp_target_version`, `ztp_image_name`
and `ztp_image_sha256` unset. XR uses the default VRF in this initial implementation.
No live schema mutations are performed by this change.

NetBox/InfraHub branch settings continue to work. If your XR platform label differs,
set `ZTP_XR_PLATFORM_SLUG`; keep it distinct from the configured NX-OS label.

## 2. Set environment and generate the bootstrap

Follow the base [Lite setup guide](ztp-lite-user-guide.md) for host networking,
passwords, dashboard login and the chosen inventory provider. Add to `.env.lite`:

```dotenv
ZTP_XR_ENABLED=true
ZTP_XR_PLATFORM_SLUG=cisco_iosxr
ZTP_EXECUTION_MODE=configuration-only
```

For a mixed fleet needing NX-OS upgrades, keep `ZTP_EXECUTION_MODE=upgrade-and-configure`.
XR still receives only an `apply-config` plan and cannot request image artifacts.
Setting `planning-only` disables XR execution. Setting `ZTP_XR_ENABLED=false`
blocks new XR registration and existing XR configuration/event access.

```bash
python3 scripts/release_xr_bootstrap.py \
  --controller http://10.10.10.1 --allow-http --output deploy/bootstrap

docker compose --env-file .env.lite -f compose.lite.yaml up -d --build ztp-api nginx
```

This creates `deploy/bootstrap/iosxr.py` without replacing the NX-OS `poap.py`.
Adjust the output to match `ZTP_BOOTSTRAP_DIR` if you use a different bind mount.

## 3. Select the XR script in DHCP

For an **XR-only** scope, use `examples/iosxr/kea.json` as the Kea configuration
selected with `ZTP_KEA_CONFIG=./examples/iosxr/kea.json` in `.env.lite`.
It uses `ens3`, `10.10.10.1/24`, leases `.10–.50` and full boot URL
`http://10.10.10.1/bootstrap/iosxr.py` in both the boot field and option 67.
The final static `.100` address lies outside the DHCP pool. Start or recreate Kea
using the chosen file (after confirming there is no competing DHCP server):

```bash
docker compose --env-file .env.lite -f compose.lite.yaml --profile dhcp up -d --force-recreate kea
```

For a mixed NX-OS/XR subnet, use device reservations or separate scopes to deliver
the correct script. Do not replace the subnet-wide NX-OS boot URL with the XR URL.
The tutorial demonstrates an XR serial-derived client identifier; verify the actual
option 61 bytes with a packet capture before using that reservation method.

```bash
curl --fail http://10.10.10.1/bootstrap/iosxr.py -o /tmp/iosxr.py
head -1 /tmp/iosxr.py
```

Expected first line: `#!/usr/bin/python3`.

## 4. Test provisioning

Use a new/unconfigured XRv9000 and its console. Release-specific commands to clean
or initiate ZTP must be checked on that release; do not erase a running router as
part of a connectivity check. The expected workflow is:

1. DHCP selects the XR bootstrap, downloaded through Nginx.
2. Bootstrap discovers chassis identity and registers with the API.
3. Controller checks the selected inventory, renders the XR template and returns
   a configuration-only manifest with a SHA-256 digest and size.
4. Bootstrap verifies the config, saves an attempt checkpoint, reports
   `CONFIG_APPLYING`, and calls `ZtpHelpers.xrapply` to apply/commit it.
5. On helper success it records a committed checkpoint and reports `CONFIG_COMMITTED`.

The dashboard shows **Apply started · outcome unconfirmed** until the commit
callback arrives, then **Commit reported · verify manually**. This does not mark
the attempt independently verified. Check hostname, running configuration,
management address, route, SSH access and `show ztp status` from the console.

Applying a static management IP can interrupt the callback. The final address must
have a route back to the controller if reporting is to succeed. A failed callback
after successful apply does not trigger reapplication or report a false install
failure. Router-side logs remain necessary for a definitive outcome.

## Recovery and reprovisioning

Checkpoints live under `/misc/disk1/ztp-lite`, keyed by controller attempt ID and
config digest, with no credentials. A committed checkpoint prevents duplicate
configuration application. An interrupted `applying` checkpoint stops with an
uncertain outcome: inspect the router before resetting. This conservative behavior
also applies if the controller became unreachable before the apply call.

After deliberately clearing the previous run/configuration, use **Reset for
reprovisioning** in the device row and restart ZTP using the release-appropriate
procedure. The new attempt reads updated inventory/templates and gets a new
checkpoint ID. The UI reset itself never erases or reloads the router.

## References

- [XRdocs Kea ZTP lab tutorial](https://xrdocs.io/ztp/tutorials/ztp-lab-tutorial-isc-kea)
- [Cisco classic ZTP helper API](https://www.cisco.com/c/en/us/td/docs/routers/asr9000/software/26xx/system-setup/configuration/guide/b-system-setup-cg-asr9000-26xx/provision-ztp.html)

The helper reference is not an XRv9000 qualification statement. Runtime and CLI
output must be verified on the intended XRv9000 release before operational use.
