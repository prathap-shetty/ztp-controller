# IOS XE configuration-only lab support

Status: opt-in implementation with simulated bootstrap and controller tests.
**Catalyst 8000V on IOS XE 17.x is the initial lab target, not yet runtime
qualified.** No IOS XE image download, release change or software installation is
implemented. The device keeps its installed release. NX-OS and IOS XR behavior
remain separate.

## Prerequisites and limits

- IOS XE ZTP runs the DHCP option 67 script in Guest Shell with Python 3 and the
  `cli` module. Guest Shell must be supported on your release and must be able to
  reach the controller over HTTP during ZTP. On Catalyst 8000V, which has no
  dedicated management port, confirm this in the lab before relying on it.
- Initial support is IPv4 in the default VRF with one management interface, such
  as `GigabitEthernet1`. The management interface must be the one that obtained
  the ZTP DHCP lease. `Mgmt-vrf` and the Catalyst 9000 `GigabitEthernet0/0`
  management port are not supported yet.
- Discovery requires a `Chassis` inventory record with PID/SN, an
  `IOS XE Software, Version` line, exactly one interface that is up with method
  `DHCP` in `show ip interface brief`, and a dotted MAC address. Unknown output
  formats fail rather than guessing.
- The PID and interface name in inventory must match the device. Virtual serial
  numbers may change after recreating the VM; update inventory accordingly.
- This is the existing Lite HTTP/password/manual-validation deployment. No image
  catalog entries or firmware are needed for IOS XE.

## 1. Configure inventory

### Local YAML

Copy `inventory/devices.iosxe.example.yaml` into your ignored local
`inventory/devices.yaml`. Replace serial, model, hostname and management address;
then set `ztp_enabled: true`. Use the exact chassis PID from `show inventory`.

```yaml
schema_version: 1
devices:
  - device:
      id: lab-c8000v-01
      name: lab-c8000v-01
      serial_number: REPLACE_WITH_CHASSIS_SERIAL
      vendor: cisco
      platform: iosxe
      model: C8000V
      status: staged
    ztp_enabled: true
    management:
      address: 10.10.10.110/24
      gateway: 10.10.10.1
      interface: GigabitEthernet1
      vrf: default
```

Omit `software` entirely; IOS XE rejects software intent. The template
`templates/cisco/iosxe_lite.j2` sets hostname, admin user, SSH (RSA key label
`ZTP-LITE-SSH`), VTY login, management address and default route. Customize it
for additional configuration and rebuild the API.

### NetBox

Set platform slug `cisco_iosxe`, manufacturer slug `cisco`, status `staged`, and
the real serial/PID. Assign the primary IPv4 address to the management interface.
Use this config context, without NX-OS image/version fields:

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "management": {
      "interface": "GigabitEthernet1",
      "vrf": "default",
      "gateway": "10.10.10.1"
    }
  }
}
```

### InfraHub

Use the existing ZTP schema extension with platform name `cisco_iosxe`. Set
`mgmt_interface` to the real interface, assign the primary address to it, set
`ztp_enabled=true` and `ztp_gateway`, and leave the image and version fields unset.

If your platform label differs, set `ZTP_XE_PLATFORM_SLUG`; keep it distinct from
the NX-OS and IOS XR labels.

## 2. Set environment and generate the bootstrap

```bash
ZTP_XE_ENABLED=true
ZTP_XE_PLATFORM_SLUG=cisco_iosxe
ZTP_EXECUTION_MODE=configuration-only
```

`ZTP_XR_ENABLED` does not enable IOS XE, and the reverse also holds. Setting
`planning-only` disables IOS XE execution.

```bash
python3 scripts/release_xe_bootstrap.py \
  --controller http://10.10.10.1 --allow-http --output deploy/bootstrap

docker compose --env-file .env.lite -f compose.lite.yaml up -d --build ztp-api nginx
```

This creates `deploy/bootstrap/iosxe.py` without replacing `poap.py` or `iosxr.py`.

## 3. Select the IOS XE script in DHCP

For an IOS XE-only scope, set `ZTP_KEA_CONFIG=./examples/iosxe/kea.json`. It gives
the full URL `http://10.10.10.1/bootstrap/iosxe.py` in option 67. For a mixed
subnet, use reservations or a separate VLAN (see the two-VLAN section of the user
guide); do not replace the NX-OS boot URL for the whole subnet.

```bash
curl --fail http://10.10.10.1/bootstrap/iosxe.py -o /tmp/iosxe.py
head -1 /tmp/iosxe.py
```

## 4. Test provisioning

Use a new or erased Catalyst 8000V with no startup configuration and its console.
The expected workflow is:

1. DHCP gives option 67; IOS XE downloads `iosxe.py` and runs it in Guest Shell.
2. The bootstrap discovers chassis identity and registers with the API.
3. The controller checks inventory, renders the template and returns a
   configuration-only manifest with a SHA-256 digest and size.
4. The bootstrap verifies the config, saves a checkpoint, reports
   `CONFIG_APPLYING`, applies the lines with `cli.configure` and runs `write memory`.
5. On success it records a committed checkpoint and reports `CONFIG_COMMITTED`.

The dashboard shows **Apply started · outcome unconfirmed**, then **Commit reported
· verify manually**. This is a device report, not independent verification. Check
hostname, running configuration, management address, route and SSH access.

IOS XE applies configuration line by line. If a line is rejected, earlier lines
remain in the running configuration and the attempt reports `FAILED`. Applying the
static management IP can interrupt the callback; a failed callback after a saved
apply does not trigger reapplication.

## Recovery and reprovisioning

Checkpoints live in `ztp-lite/` on bootflash (`/flash` in Guest Shell), keyed by
attempt ID and config digest, with no credentials. A committed checkpoint prevents
duplicate application. An `applying` checkpoint, including one left by a failed
apply or save, stops with an uncertain outcome: inspect the device before resetting.

After deliberately erasing the device, delete `bootflash:ztp-lite/` if it survived,
use **Reset for reprovisioning** in the dashboard, and restart ZTP. The UI reset
never erases or reloads the device.

## References

- [Cisco IOS XE ZTP troubleshooting (Catalyst 9300)](https://www.cisco.com/c/en/us/support/docs/switches/catalyst-9300-switch/220634-configure-and-troubleshoot-ztp-on-cataly.html)
- [Device provisioning with IOS XE ZTP](https://blogs.cisco.com/developer/device-provisioning-with-ios-xe-zero-touch-provisioning)

These references are not a Catalyst 8000V qualification statement. Verify Guest
Shell networking and CLI output on the intended release before operational use.
