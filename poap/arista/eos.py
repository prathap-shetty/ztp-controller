#!/usr/bin/env python3
"""ZTP Lite Arista EOS configuration-only bootstrap; stages startup-config for the ZTP reboot."""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CONTROLLER_URL = "https://controller.example.invalid"
ALLOW_HTTP = False
FLASH = "/mnt/flash"
CLI_COMMANDS = (("/usr/bin/FastCli", "-p", "15", "-c"), ("/usr/bin/Cli", "-p", "15", "-c"))
MANAGEMENT = r"Management[0-9]+(?:/[0-9]+)?"
TOKEN = r"[A-Za-z0-9_.():-]{1,128}"
# vEOS/cEOS may report an empty serial; only these models fall back to the system MAC.
VIRTUAL_MODEL = re.compile(r"^[vc]EOS", re.I)


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


def show(command):
    for prefix in CLI_COMMANDS:
        if not os.path.exists(prefix[0]):
            continue
        try:
            output = subprocess.check_output(
                list(prefix) + [command + " | json"], stderr=subprocess.DEVNULL, timeout=120
            )
            return json.loads(output.decode())
        except Exception:
            raise BootstrapError("Device discovery command failed") from None
    raise BootstrapError("EOS CLI not found")


def mac_address(value):
    raw = re.sub(r"[^0-9a-fA-F]", "", value or "").lower()
    if len(raw) != 12:
        return None
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def discover():
    version = show("show version")
    model = str(version.get("modelName") or "")
    release = str(version.get("version") or "")
    serial = str(version.get("serialNumber") or "").strip()
    system_mac = mac_address(version.get("systemMacAddress"))
    if not serial and VIRTUAL_MODEL.match(model) and system_mac:
        serial = system_mac.replace(":", "")
    if not all(re.fullmatch(TOKEN, value) for value in (model, release, serial)):
        raise BootstrapError("Cannot identify EOS model, version and serial")
    interfaces = show("show interfaces").get("interfaces", {})
    names = [
        name
        for name, details in interfaces.items()
        if re.fullmatch(MANAGEMENT, name) and details.get("lineProtocolStatus") == "up"
    ]
    if len(names) != 1:
        raise BootstrapError("Cannot identify a unique management interface that is up")
    mac = mac_address(interfaces[names[0]].get("physicalAddress"))
    if not mac:
        raise BootstrapError("Cannot identify management MAC")
    return {
        "serial_number": serial.upper(),
        "model": model.upper(),
        "vendor": "arista",
        "platform": "eos",
        "current_version": release,
        "management_mac": mac,
    }, names[0]


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


def write_file(path, content, mode):
    with open(path + ".tmp", "wb") as handle:
        os.chmod(path + ".tmp", mode)
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(path + ".tmp", path)


def stage(config):
    path = os.path.join(FLASH, "startup-config")
    # ZTP only runs without a startup-config; never overwrite one that appeared since.
    if os.path.exists(path):
        return False
    try:
        write_file(path, config, 0o644)
    except OSError:
        return False
    return True


def apply(observed, interface):
    registration = json.loads(request("/api/v1/ztp/register", observed).decode())
    manifest = registration["manifest"]
    attempt_id = str(uuid.UUID(registration["provisioning_id"]))
    if (
        manifest.get("schema_version") != 2
        or manifest.get("platform") != "eos"
        or manifest.get("mode") != "configuration-only"
        or manifest.get("actions") != ["apply-config"]
        or manifest.get("upgrade_required") is not False
        or manifest.get("execution_enabled") is not True
        or manifest.get("serial_number") != observed["serial_number"]
        or manifest.get("management", {}).get("interface") != interface
        or manifest.get("management", {}).get("vrf") != "default"
    ):
        raise BootstrapError("Controller returned unsupported EOS plan")
    directory = os.path.join(FLASH, "ztp-lite")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    checkpoint = os.path.join(directory, attempt_id + ".json")
    digest = manifest["config"]["sha256"]
    if os.path.exists(checkpoint):
        with open(checkpoint) as handle:
            saved = json.load(handle)
        if saved != {"sha256": digest, "state": "committed"}:
            raise BootstrapError(
                "Previous apply outcome is uncertain; inspect flash and reset attempt"
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
        # Persist a pending marker first; an interrupted run must not blindly restage.
        save_checkpoint(checkpoint, digest, "applying")
        event(registration, "CONFIG_APPLYING")
        if not stage(config):
            try:
                event(registration, "FAILED")
            except Exception:
                pass
            raise BootstrapError("Could not stage startup-config; inspect flash")
        save_checkpoint(checkpoint, digest, "committed")
    # EOS loads the staged startup-config on the reboot that follows ZTP.
    try:
        event(registration, "CONFIG_COMMITTED")
    except Exception:
        print("startup-config staged; controller callback unavailable. Verify after reboot.")
    return 0


def save_checkpoint(path, digest, state):
    write_file(path, json.dumps({"sha256": digest, "state": state}).encode(), 0o600)


def main():
    observed, interface = discover()
    return apply(observed, interface)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Avoid device output/config bodies and unexpected exception messages.
        print(
            "ZTP Lite EOS failed: "
            + (
                str(exc)
                if isinstance(exc, BootstrapError)
                else type(exc).__name__ + "; check switch ZTP logs"
            )
        )
        sys.exit(1)
