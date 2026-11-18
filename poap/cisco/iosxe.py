#!/usr/bin/env python3
"""ZTP Lite IOS XE configuration-only bootstrap; runs in Guest Shell with the cli module."""

import hashlib
import json
import os
import re
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CONTROLLER_URL = "https://controller.example.invalid"
ALLOW_HTTP = False
# Guest Shell mounts IOS bootflash here; the checkpoint must survive a rerun.
CHECKPOINT_ROOTS = ("/flash", "/bootflash")
INTERFACE = r"(?:GigabitEthernet|TenGigabitEthernet)[0-9]+(?:/[0-9]+){0,2}"


class BootstrapError(Exception):
    """A sanitized, locally defined operator diagnostic."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BootstrapError("Controller redirects are not supported")


def request(path, data=None, token=None, limit=1048576):
    if CONTROLLER_URL.startswith("http://") and not ALLOW_HTTP:
        raise BootstrapError("HTTP controller requires explicit opt-in")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    payload = json.dumps(data).encode() if data is not None else None
    req = Request(CONTROLLER_URL + path, data=payload, headers=headers)
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=30) as response:
            content = response.read(limit + 1)
            if len(content) > limit:
                raise BootstrapError("Controller response exceeds size limit")
            return content
    except HTTPError as exc:
        # Never print response bodies: inventory and credentials may be present.
        raise BootstrapError("Controller rejected request: HTTP " + str(exc.code)) from None


def command(cli, text):
    try:
        output = cli.execute(text)
    except Exception:
        raise BootstrapError("Device discovery command failed") from None
    if not isinstance(output, str):
        raise BootstrapError("Device discovery command failed")
    return output


def discover(cli):
    inventory = command(cli, "show inventory")
    candidates = []
    for name, pid, serial in re.findall(
        r'NAME:\s*"([^"]+)"[^\n]*\n\s*PID:\s*([^,\s]+)[^\n]*?SN:\s*([^\s,]+)',
        inventory,
        re.I,
    ):
        if name.lower() == "chassis":
            candidates.append((pid, serial))
    if len(candidates) != 1:
        raise BootstrapError("Cannot uniquely identify chassis PID and serial from inventory")
    version = re.search(
        r"Cisco IOS[ -]XE Software,?\s+Version\s+([0-9][A-Za-z0-9.()-]*)",
        command(cli, "show version"),
    )
    # The interface that obtained the ZTP lease is the management interface.
    names = re.findall(
        r"^(" + INTERFACE + r")\s+\S+\s+\S+\s+DHCP\s+up\s+up",
        command(cli, "show ip interface brief"),
        re.M,
    )
    if not version or len(set(names)) != 1:
        raise BootstrapError("Cannot identify XE version and unique DHCP management interface")
    name = names[0]
    details = command(cli, "show interfaces " + name)
    mac = re.search(r"address (?:is )?([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})", details)
    if not mac:
        raise BootstrapError("Cannot identify management MAC")
    raw = mac.group(1).replace(".", "").lower()
    pid, serial = candidates[0]
    return {
        "serial_number": serial.upper(),
        "model": pid.upper(),
        "vendor": "cisco",
        "platform": "iosxe",
        "current_version": version.group(1),
        "management_mac": ":".join(raw[i : i + 2] for i in range(0, 12, 2)),
    }, name


def event(registration, kind):
    payload = {
        "event_id": str(uuid.uuid4()),
        "event": kind,
        "config_sha256": registration["manifest"]["config"]["sha256"],
    }
    for attempt in range(3):
        try:
            request(
                "/api/v1/ztp/status/" + registration["provisioning_id"],
                payload,
                registration["status_token"],
            )
            return
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2)


def checkpoint_directory():
    for root in CHECKPOINT_ROOTS:
        if os.path.isdir(root):
            return os.path.join(root, "ztp-lite")
    raise BootstrapError("No persistent flash directory for the apply checkpoint")


def configure(cli, config):
    lines = [
        line.rstrip()
        for line in config.decode().splitlines()
        if line.strip() and not line.lstrip().startswith("!") and line.strip() != "end"
    ]
    try:
        results = cli.configure(lines)
    except Exception:
        return False
    return bool(results) and all(getattr(result, "success", False) for result in results)


def save(cli):
    try:
        return "[OK]" in cli.execute("write memory")
    except Exception:
        return False


def apply(cli, observed, interface):
    registration = json.loads(request("/api/v1/ztp/register", observed).decode())
    manifest = registration["manifest"]
    attempt_id = str(uuid.UUID(registration["provisioning_id"]))
    if (
        manifest.get("schema_version") != 2
        or manifest.get("platform") != "iosxe"
        or manifest.get("mode") != "configuration-only"
        or manifest.get("actions") != ["apply-config"]
        or manifest.get("upgrade_required") is not False
        or manifest.get("execution_enabled") is not True
        or manifest.get("serial_number") != observed["serial_number"]
        or manifest.get("management", {}).get("interface") != interface
        or manifest.get("management", {}).get("vrf") != "default"
    ):
        raise BootstrapError("Controller returned unsupported XE plan")
    directory = checkpoint_directory()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    checkpoint = os.path.join(directory, attempt_id + ".json")
    digest = manifest["config"]["sha256"]
    if os.path.exists(checkpoint):
        with open(checkpoint) as handle:
            saved = json.load(handle)
        if saved != {"sha256": digest, "state": "committed"}:
            raise BootstrapError(
                "Previous apply outcome is uncertain; inspect device and reset attempt"
            )
    else:
        config = request(
            "/api/v1/ztp/config/" + attempt_id, token=registration["status_token"], limit=262144
        )
        if (
            len(config) != manifest["config"]["size_bytes"]
            or hashlib.sha256(config).hexdigest() != digest
        ):
            raise BootstrapError("Configuration integrity check failed")
        # Persist a pending marker before applying; an interrupted run must not blindly reapply.
        save_checkpoint(checkpoint, digest, "applying")
        event(registration, "CONFIG_APPLYING")
        # IOS XE applies line by line; lines before a rejected one stay in running config.
        if not configure(cli, config) or not save(cli):
            try:
                event(registration, "FAILED")
            except Exception:
                pass
            raise BootstrapError("XE configuration apply or save failed; inspect device")
        save_checkpoint(checkpoint, digest, "committed")
    # A successful apply may change the management address and break the callback.
    # Do not report failure or reapply a saved config just because reporting failed.
    try:
        event(registration, "CONFIG_COMMITTED")
    except Exception:
        print("Configuration saved; controller callback unavailable. Verify manually.")
    return 0


def save_checkpoint(path, digest, state):
    with open(path + ".tmp", "w") as handle:
        os.chmod(path + ".tmp", 0o600)
        json.dump({"sha256": digest, "state": state}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(path + ".tmp", path)


def main():
    import cli

    observed, interface = discover(cli)
    return apply(cli, observed, interface)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Avoid device output/config bodies and unexpected exception messages.
        print(
            "ZTP Lite XE failed: "
            + (
                str(exc)
                if isinstance(exc, BootstrapError)
                else type(exc).__name__ + "; check device ZTP logs"
            )
        )
        sys.exit(1)
