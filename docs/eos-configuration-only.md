# Arista EOS configuration-only lab support

Status: opt-in implementation with simulated bootstrap and controller tests.
**vEOS-lab is the initial lab target, not yet runtime qualified.** No EOS image
download, release change or software installation is implemented. The switch
keeps its installed release. Cisco behavior is unchanged.

## How it works

EOS ZTP downloads the DHCP option 67 file. A file whose first line starts with
`#!` runs as a script; the switch reboots after it finishes. ZTP does not start
if flash already holds a `startup-config` or `zerotouch-config`.

The bootstrap does not change the running configuration. It writes the rendered
configuration to `/mnt/flash/startup-config`, and EOS loads it on the reboot that
follows ZTP. Because the management address only changes after that reboot, the
controller callback normally arrives before the address changes.

## Prerequisites and limits

- The switch must provide Python 3 for `#!/usr/bin/env python3` and `FastCli` or
  `Cli` in `/usr/bin`. Confirm both on your EOS release (`bash which python3`)
  before testing; a missing interpreter fails before the controller is contacted.
- Initial support is IPv4 in the default VRF with one management interface, such
  as `Management1`. A management VRF and front-panel `Ethernet` management are not
  supported yet.
- Discovery requires `show version | json` with `modelName`, `version` and a
  serial, and exactly one `Management` interface whose line protocol is up.
- **vEOS serial numbers.** vEOS often reports an empty serial. Only for models
  starting with `vEOS` or `cEOS`, the bootstrap then uses the system MAC from
  `show version` without separators, in upper case (for example `5254001A2B3C`).
  Put that value in inventory. Physical switches with an empty serial fail.
- This is the existing Lite HTTP/password/manual-validation deployment. No image
  catalog entries or firmware are needed for EOS.

## 1. Configure inventory

### Local YAML

Copy `inventory/devices.eos.example.yaml` into your ignored local
`inventory/devices.yaml`. Replace serial, model, hostname and management address;
then set `ztp_enabled: true`. Use `modelName` from `show version`, in upper case.

```yaml
schema_version: 1
devices:
  - device:
      id: lab-veos-01
      name: lab-veos-01
      serial_number: 5254001A2B3C
      vendor: arista
      platform: eos
      model: VEOS-LAB
      status: staged
    ztp_enabled: true
    management:
      address: 10.10.10.120/24
      gateway: 10.10.10.1
      interface: Management1
      vrf: default
```

Omit `software` entirely; EOS rejects software intent. The template
`templates/arista/eos_lite.j2` sets hostname, admin user, management address,
default route and SSH. Customize it for additional configuration and rebuild the API.

### NetBox

Set platform slug `arista_eos`, manufacturer slug `arista`, status `staged`, and
the serial and model as above. Assign the primary IPv4 address to `Management1`.
Use this config context, without image/version fields:

```json
{
  "provisioning": {"ztp_enabled": true},
  "ztp": {
    "management": {
      "interface": "Management1",
      "vrf": "default",
      "gateway": "10.10.10.1"
    }
  }
}
```

### InfraHub

Use the existing ZTP schema extension with platform name `arista_eos` and
manufacturer `Arista`. Set `mgmt_interface`, assign the primary address to it, set
`ztp_enabled=true` and `ztp_gateway`, and leave the image and version fields unset.

If your platform label differs, set `ZTP_EOS_PLATFORM_SLUG`; keep it distinct from
the Cisco labels.

## 2. Set environment and generate the bootstrap

```bash
ZTP_EOS_ENABLED=true
ZTP_EOS_PLATFORM_SLUG=arista_eos
ZTP_EXECUTION_MODE=configuration-only
```

EOS enablement is independent of `ZTP_XR_ENABLED` and `ZTP_XE_ENABLED`. Setting
`planning-only` disables EOS execution.

```bash
python3 scripts/release_eos_bootstrap.py \
  --controller http://10.10.10.1 --allow-http --output deploy/bootstrap

docker compose --env-file .env.lite -f compose.lite.yaml up -d --build ztp-api nginx
```

This creates `deploy/bootstrap/eos.py` without replacing the Cisco scripts.

## 3. Select the EOS script in DHCP

For an EOS-only scope, set `ZTP_KEA_CONFIG=./examples/eos/kea.json`. It gives the
full URL `http://10.10.10.1/bootstrap/eos.py` in option 67. For a subnet shared with
other platforms, use the client-class example in the user guide's mixed-platform section.

```bash
curl --fail http://10.10.10.1/bootstrap/eos.py -o /tmp/eos.py
head -1 /tmp/eos.py
```

## 4. Test provisioning

Use a vEOS with no `startup-config` and its console. The expected workflow is:

1. DHCP gives option 67; EOS downloads `eos.py` and runs it.
2. The bootstrap discovers model, version, serial and management interface, then
   registers with the API.
3. The controller checks inventory, renders the template and returns a
   configuration-only manifest with a SHA-256 digest and size.
4. The bootstrap verifies the config, saves a checkpoint, reports
   `CONFIG_APPLYING`, writes `startup-config` and reports `CONFIG_COMMITTED`.
5. EOS reboots and loads the staged configuration.

The dashboard shows **Staging started · outcome unconfirmed**, then **Startup config
staged · verify after reboot**. Staged is not verified: after the reboot, check
hostname, management address, route and SSH access. EOS skips configuration lines
it cannot parse at boot, so review `show logging` for configuration errors.

## Recovery and reprovisioning

Checkpoints live in `/mnt/flash/ztp-lite/`, keyed by attempt ID and config digest,
with no credentials. A committed checkpoint prevents restaging. An `applying`
checkpoint stops with an uncertain outcome: inspect flash before resetting. The
bootstrap never overwrites an existing `startup-config`.

If the script fails, stop ZTP with `zerotouch cancel` before investigating. To
reprovision, deliberately remove `startup-config` and `/mnt/flash/ztp-lite/`, use
**Reset for reprovisioning** in the dashboard, and reload. The UI reset never
erases or reloads the switch.

## References

- [Arista EOS System Reset and ZTP](https://www.arista.com/en/um-eos/eos-system-reset)
- [Arista EOS Initial Switch Access](https://www.arista.com/ko/um-eos/eos-initial-switch-access)

These references are not a vEOS qualification statement. Verify JSON output and
ZTP behavior on the intended EOS release before operational use.
