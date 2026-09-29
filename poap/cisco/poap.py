#!/usr/bin/env python3
# md5sum="GENERATED_BY_RELEASE_TOOL"
"""Configuration-only NX-OS POAP. Release with scripts/release_bootstrap.py.

Original implementation; native scheduled-config lifecycle referenced in UPSTREAM.md.
Requires Python 3, cisco.vrf.set_global_vrf, CLI JSON and qualified replay behavior.
Upgrade mode stages an approved image with native POAP no-reload installation.
No shell execution, startup erasure or direct reload commands.
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


def transport():
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
    return opener


def request(path, data=None, token=None):
    opener = transport()
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


def download_image(attempt_id, token, profile, bootflash):
    """Bounded-memory download; install only after a full size/SHA-256 check."""
    digest = profile["image_checksum"]
    size = profile["image_size_bytes"]
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or not isinstance(size, int) or size <= 0:
        raise RuntimeError("Invalid image metadata")
    name = "ztp-image-" + digest + ".bin"
    path = os.path.join(bootflash, name)

    def valid():
        if not os.path.isfile(path) or os.path.getsize(path) != size:
            return False
        h = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest() == digest

    if valid():
        return name
    space = os.statvfs(bootflash)
    if space.f_bavail * space.f_frsize < size + 256 * 1024 * 1024:
        raise RuntimeError("Insufficient bootflash space; no files were deleted")
    opener = transport()
    for attempt in range(MAX_ATTEMPTS):
        try:
            h, count = hashlib.sha256(), 0
            started = time.monotonic()
            req = Request(
                CONTROLLER_URL.rstrip("/") + "/api/v1/ztp/image/" + attempt_id,
                headers={"Authorization": "Bearer " + token},
            )
            with opener.open(req, timeout=30) as response:
                with open(path + ".part", "wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > size or time.monotonic() - started > 1800:
                            raise RuntimeError("Image transfer exceeded bounds")
                        h.update(chunk)
                        handle.write(chunk)
                    handle.flush()
                    os.fsync(handle.fileno())
            if count != size or h.hexdigest() != digest:
                raise RuntimeError("Image integrity check failed")
            os.replace(path + ".part", path)
            directory = os.open(bootflash, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return name
        except HTTPError as exc:
            if exc.code not in (429, 502, 503, 504) or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError("Image request rejected") from None
        except URLError as exc:
            if isinstance(exc.reason, ssl.SSLError) or attempt == MAX_ATTEMPTS - 1:
                raise RuntimeError("Image transport failed") from None
        finally:
            if os.path.exists(path + ".part"):
                os.unlink(path + ".part")
        time.sleep(2**attempt)
    raise RuntimeError("Image retry budget exhausted")


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


def run(cli, environ, bootflash="/bootflash", api=request, image_download=download_image):
    if environ.get("POAP_VRF") != "management":
        raise RuntimeError("Only management-VRF POAP is supported")
    observed = discover(cli, environ)
    registration = json.loads(api("/api/v1/ztp/register", observed))
    manifest = registration["manifest"]
    upgrading = manifest["mode"] == "upgrade-and-configure"
    target_running = manifest["target"]["target_version"] == observed["current_version"]
    expected_actions = (
        ["download-image", "install-image", "stage-config"]
        if upgrading and manifest["upgrade_required"]
        else ["stage-config"]
    )
    if (
        manifest["mode"] not in ("configuration-only", "upgrade-and-configure")
        or manifest["execution_enabled"] is not True
        or (not upgrading and (manifest["upgrade_required"] or not target_running))
        or manifest["actions"] != expected_actions
        or (
            upgrading
            and (
                manifest["compatibility_profile"].get("install_method") != "poap-install-no-reload"
                or manifest["compatibility_profile"].get("upgrade_path_approved") is not True
            )
        )
        or (
            not target_running
            and observed["current_version"]
            not in manifest["compatibility_profile"]["source_versions"]
        )
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
    previous_state = None
    if os.path.exists(journal):
        with open(journal) as handle:
            checkpoint = json.load(handle)
        if checkpoint.get("sha256") != artifact["sha256"] or (
            upgrading and checkpoint.get("plan_hash") != registration["plan_hash"]
        ):
            raise RuntimeError("Checkpoint conflicts with manifest")
        if checkpoint.get("state") == "staged":
            return 0
        # Power loss between the native command and its acknowledgment is ambiguous.
        # Require operator recovery instead of scheduling the same config twice.
        previous_state = checkpoint.get("state")
        if previous_state not in ("image-installed", "installing") or (
            previous_state == "installing" and not target_running
        ):
            raise RuntimeError("Interrupted scheduling requires recovery")

    def checkpoint(state):
        with open(journal + ".tmp", "w") as handle:
            json.dump(
                {
                    "state": state,
                    "sha256": artifact["sha256"],
                    "plan_hash": registration.get("plan_hash"),
                },
                handle,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(journal + ".tmp", journal)
        sync_directory()

    if upgrading and not target_running and previous_state != "image-installed":
        name = image_download(attempt_id, token, manifest["compatibility_profile"], bootflash)
        api(
            "/api/v1/ztp/status/" + attempt_id,
            {
                "event_id": str(uuid.uuid5(uuid.UUID(attempt_id), "IMAGE_VERIFIED")),
                "event": "IMAGE_VERIFIED",
                "config_sha256": artifact["sha256"],
            },
            token=token,
        )
        checkpoint("installing")
        output = cli(
            "terminal dont-ask ; install all nxos bootflash:" + name + " no-reload non-interruptive"
        )
        if re.search(r"(?im)(?:^|\n)\s*(?:%|error|failed|invalid)", output or ""):
            raise RuntimeError("Native image installation failed; recovery required")
        checkpoint("image-installed")
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
    # Exit success hands control to native POAP for reboot and post-upgrade replay.
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
