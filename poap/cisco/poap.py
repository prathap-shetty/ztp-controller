#!/usr/bin/env python3
# md5sum="GENERATED_BY_RELEASE_TOOL"
"""Configuration-only NX-OS POAP. Release with scripts/release_bootstrap.py.

Original implementation; native scheduled-config lifecycle referenced in UPSTREAM.md.
Requires Python 3, cisco.vrf.set_global_vrf, CLI JSON and qualified replay behavior.
No image installation, shell execution, startup erasure or direct reload commands.
"""

import hashlib
import json
import os
import re
import ssl
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

# Replaced at release time. No credentials belong in this public script.
CONTROLLER_URL = "https://controller.example.invalid"
ALLOW_HTTP = False
CA_PEM = ""
MAX_ATTEMPTS = 3


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("Redirects are not permitted")


def request(path, data=None, token=None):
    parsed = urlsplit(CONTROLLER_URL)
    if (
        parsed.scheme not in ("https", "http")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise RuntimeError("Invalid controller origin")
    if parsed.scheme == "http" and not ALLOW_HTTP:
        raise RuntimeError("HTTP requires an explicit isolated-lab release")
    context = ssl.create_default_context(cadata=CA_PEM or None)
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    body = json.dumps(data).encode() if data is not None else None
    for attempt in range(MAX_ATTEMPTS):
        try:
            with opener.open(
                Request(CONTROLLER_URL.rstrip("/") + path, data=body, headers=headers), timeout=15
            ) as response:
                content = response.read(262145)
                if len(content) > 262144:
                    raise RuntimeError("Oversized response")
                return content
        except HTTPError as exc:
            if exc.code not in (429, 502, 503, 504) or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError("Controller rejected request: " + str(exc.code)) from None
        except URLError as exc:
            if isinstance(exc.reason, ssl.SSLError) or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError("Controller transport failed") from None
        time.sleep(2**attempt)
    raise RuntimeError("Retry budget exhausted")


def discover(cli, environ):
    version = json.loads(cli("show version | json"))
    inventory = json.loads(cli("show inventory | json"))
    rows = inventory["TABLE_inv"]["ROW_inv"]
    if isinstance(rows, dict):
        rows = [rows]
    chassis = [r for r in rows if r.get("name", "").strip('"').lower() == "chassis"]
    if len(chassis) != 1:
        raise RuntimeError("Ambiguous chassis identity")
    serial = chassis[0]["serialnum"].strip().upper()
    if environ.get("POAP_SERIAL", serial).strip().upper() != serial:
        raise RuntimeError("POAP serial does not match chassis")
    mac = environ.get("POAP_MGMT_MAC") or environ.get("POAP_MAC", "")
    mac = re.sub(r"[.:-]", "", mac).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", mac):
        raise RuntimeError("Missing POAP management MAC")
    return {
        "serial_number": serial,
        "vendor": "cisco",
        "platform": "nxos",
        "model": chassis[0]["productid"].strip().upper(),
        "current_version": (version.get("nxos_ver_str") or version["kickstart_ver_str"]).strip(),
        "management_mac": ":".join(mac[i : i + 2] for i in range(0, 12, 2)),
    }


def run(cli, environ, bootflash="/bootflash", api=request):
    if environ.get("POAP_VRF") != "management":
        raise RuntimeError("Only management-VRF POAP is supported")
    observed = discover(cli, environ)
    registration = json.loads(api("/api/v1/ztp/register", observed))
    manifest = registration["manifest"]
    if (
        manifest["mode"] != "configuration-only"
        or manifest["execution_enabled"] is not True
        or manifest["upgrade_required"]
        or manifest["actions"] != ["stage-config"]
        or manifest["target"]["target_version"] != observed["current_version"]
        or manifest["serial_number"] != observed["serial_number"]
        or manifest["compatibility_profile"]["model"] != observed["model"]
        or manifest["compatibility_profile"]["replay_method"] != "scheduled-config-exit"
        or registration["state"] not in ("AUTHORIZED", "CONFIGURING", "VALIDATING")
    ):
        raise RuntimeError("Not an eligible configuration-only manifest")
    attempt_id = str(uuid.UUID(registration["provisioning_id"]))
    token, artifact = registration["status_token"], manifest["config"]
    data = api("/api/v1/ztp/config/" + attempt_id, token=token)
    if (
        len(data) != artifact["size_bytes"]
        or hashlib.sha256(data).hexdigest() != artifact["sha256"]
    ):
        raise RuntimeError("Configuration integrity check failed")
    # Write atomically and durably before asking native POAP to schedule replay.
    destination = "ztp-" + attempt_id + ".cfg"
    path = os.path.join(bootflash, destination)
    descriptor = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())

    def sync_directory():
        directory = os.open(bootflash, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    os.replace(path + ".tmp", path)
    sync_directory()
    journal = path + ".json"
    if os.path.exists(journal):
        with open(journal) as handle:
            checkpoint = json.load(handle)
        if checkpoint.get("sha256") != artifact["sha256"]:
            raise RuntimeError("Checkpoint conflicts with manifest")
        if checkpoint.get("state") == "staged":
            return 0
        # Power loss between the native command and its acknowledgment is ambiguous.
        # Require operator recovery instead of scheduling the same config twice.
        raise RuntimeError("Interrupted scheduling requires recovery")

    def checkpoint(state):
        with open(journal + ".tmp", "w") as handle:
            json.dump({"state": state, "sha256": artifact["sha256"]}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(journal + ".tmp", journal)
        sync_directory()

    checkpoint("scheduling")
    output = cli("copy bootflash:" + destination + " scheduled-config")
    if re.search(r"(?im)(?:^|\n)\s*(?:%|error|failed|invalid)", output or ""):
        raise RuntimeError("Native configuration scheduling failed")
    checkpoint("staged")
    # If this callback is lost, the durable controller validation job still runs.
    try:
        api(
            "/api/v1/ztp/status/" + attempt_id,
            {
                "event_id": str(uuid.uuid5(uuid.UUID(attempt_id), "CONFIG_STAGED")),
                "event": "CONFIG_STAGED",
                "config_sha256": artifact["sha256"],
            },
            token=token,
        )
    except (RuntimeError, OSError):
        pass
    # Exit success hands control to native POAP replay. Never issue install/reload.
    return 0


def main():
    from cisco.vrf import set_global_vrf
    from cli import cli

    if os.environ.get("POAP_VRF") != "management":
        raise RuntimeError("Unsupported POAP VRF")
    set_global_vrf("management")
    return run(cli, os.environ)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Do not log URLs/tokens or arbitrary CLI/config output.
        sys.stderr.write("ZTP configuration-only bootstrap failed; inspect controller attempt.\n")
        sys.exit(1)
