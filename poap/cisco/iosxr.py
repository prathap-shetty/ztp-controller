#!/usr/bin/python3
"""ZTP Lite IOS XR configuration-only bootstrap; requires Python 3 and ZtpHelpers."""

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import types
import uuid
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

CONTROLLER_URL = "https://controller.example.invalid"
ALLOW_HTTP = False
CHECKPOINT_DIRECTORY = "/misc/disk1/ztp-lite"


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


def command(helper, text):
    result = helper.xrcmd({"exec_cmd": text})
    if not isinstance(result, dict) or result.get("status") != "success":
        raise BootstrapError("Device discovery command failed")
    output = result.get("output", "")
    return "\n".join(output) if isinstance(output, list) else output


def discover(helper):
    inventory = command(helper, "show inventory")
    candidates = []
    for name, pid, serial in re.findall(
        r'NAME:\s*"([^"]+)"[^\n]*\n\s*PID:\s*([^,\s]+)[^\n]*?SN:\s*([^\s,]+)',
        inventory,
        re.I,
    ):
        if name.lower() in {"chassis", "rack 0", "0"}:
            candidates.append((pid, serial))
    if len(candidates) != 1:
        raise BootstrapError("Cannot uniquely identify chassis PID and serial from inventory")
    version = re.search(
        r"(?:IOS XR Software,?\s+Version|Version)\s*:?\s*([0-9][A-Za-z0-9.()-]*)",
        command(helper, "show version"),
    )
    # "show interfaces brief" abbreviates the port as Mg0/RP0/CPU0/0 on XR 25.x;
    # the IPv4 brief view lists the full MgmtEth name on every release.
    interface = command(helper, "show ipv4 interface brief")
    names = re.findall(r"\b(MgmtEth[0-9]+/(?:RP[0-9]+|[0-9]+)/CPU[0-9]+/[0-9]+)\b", interface)
    if not version or len(set(names)) != 1:
        raise BootstrapError("Cannot identify XR version and unique management interface")
    name = names[0]
    details = command(helper, "show interfaces " + name)
    mac = re.search(r"address (?:is )?([0-9a-fA-F]{4}\.[0-9a-fA-F]{4}\.[0-9a-fA-F]{4})", details)
    if not mac:
        raise BootstrapError("Cannot identify management MAC")
    raw = mac.group(1).replace(".", "").lower()
    pid, serial = candidates[0]
    return {
        "serial_number": serial.upper(),
        "model": pid.upper(),
        "vendor": "cisco",
        "platform": "iosxr",
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


def apply(helper, observed, interface):
    registration = json.loads(request("/api/v1/ztp/register", observed))
    manifest = registration["manifest"]
    attempt_id = str(uuid.UUID(registration["provisioning_id"]))
    if (
        manifest.get("schema_version") != 2
        or manifest.get("platform") != "iosxr"
        or manifest.get("mode") != "configuration-only"
        or manifest.get("actions") != ["apply-config"]
        or manifest.get("upgrade_required") is not False
        or manifest.get("execution_enabled") is not True
        or manifest.get("serial_number") != observed["serial_number"]
        or manifest.get("management", {}).get("interface") != interface
        or manifest.get("management", {}).get("vrf") != "default"
    ):
        raise BootstrapError("Controller returned unsupported XR plan")
    os.makedirs(CHECKPOINT_DIRECTORY, mode=0o700, exist_ok=True)
    checkpoint = os.path.join(CHECKPOINT_DIRECTORY, attempt_id + ".json")
    digest = manifest["config"]["sha256"]
    if os.path.exists(checkpoint):
        with open(checkpoint) as handle:
            saved = json.load(handle)
        if saved != {"sha256": digest, "state": "committed"}:
            raise BootstrapError(
                "Previous apply outcome is uncertain; inspect router and reset attempt"
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
        with tempfile.TemporaryDirectory(prefix="ztp-lite-") as folder:
            path = os.path.join(folder, "config.cfg")
            with open(path, "wb") as handle:
                os.chmod(path, 0o600)
                handle.write(config)
            result = helper.xrapply(filename=path, reason="ZTP Lite " + attempt_id)
        if not isinstance(result, dict) or result.get("status") != "success":
            try:
                event(registration, "FAILED")
            except Exception:
                pass
            raise BootstrapError("XR configuration apply failed; inspect show configuration failed")
        save_checkpoint(checkpoint, digest, "committed")
    # A successful commit may change the management address and break the callback.
    # Do not report failure or reapply a committed config just because reporting failed.
    try:
        event(registration, "CONFIG_COMMITTED")
    except Exception:
        print("Configuration committed; controller callback unavailable. Verify manually.")
    return 0


def save_checkpoint(path, digest, state):
    with open(path + ".tmp", "w") as handle:
        os.chmod(path + ".tmp", 0o600)
        json.dump({"sha256": digest, "state": state}, handle)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(path + ".tmp", path)


HELPER_PATH = "/pkg/bin"


def load_helpers():
    """Import Cisco ZtpHelpers, tolerating a broken optional NETCONF dependency.

    On XR 25.1.1, ztp_helper imports ztp_netconf, whose netconf_client_ztp_lib is
    shipped only as bytecode for another Python version ("bad magic number").
    ZTP Lite uses only xrcmd, xrapply and the namespace helpers, never NETCONF,
    so retry once with a ztp_netconf placeholder that fails if it is ever called.
    """
    if HELPER_PATH not in sys.path:
        sys.path.append(HELPER_PATH)
    try:
        from ztp_helper import ZtpHelpers
    except ImportError as exc:
        if "netconf" not in str(exc).lower():
            raise
        reason = str(exc)[:120]
        for name in ("ztp_helper", "ztp_netconf", "netconf_client_ztp_lib"):
            sys.modules.pop(name, None)

        def unavailable(*args, **kwargs):
            raise BootstrapError("XR NETCONF helper is unavailable: " + reason)

        placeholder = types.ModuleType("ztp_netconf")
        placeholder.xr_netconf = placeholder.xr_netconf_proxy_stop = unavailable
        sys.modules["ztp_netconf"] = placeholder
        from ztp_helper import ZtpHelpers
    return ZtpHelpers


def main():
    helper = load_helpers()()
    helper.toggle_debug(False)
    observed, interface = discover(helper)
    enter_global_vrf(helper)
    return apply(helper, observed, interface)


def enter_global_vrf(helper):
    """Enter the default-VRF namespace that the ZTP transport uses, for urllib.

    Older ZtpHelpers.setns takes a file descriptor; XR 25.1.1 takes the open file
    and calls fileno() itself. Try the descriptor first, as before.
    """
    with open(helper.get_netns_path(nsname="global-vrf")) as namespace:
        try:
            helper.setns(namespace.fileno(), 0)
        except AttributeError as exc:
            if "fileno" not in str(exc):
                raise
            helper.setns(namespace, 0)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        # Avoid helper response/config bodies and unexpected exception messages.
        print(
            "ZTP Lite XR failed: "
            + (
                str(exc)
                if isinstance(exc, BootstrapError)
                # Import errors name modules and paths only; show them for diagnosis.
                else "ImportError: " + str(exc)[:200]
                if isinstance(exc, ImportError)
                else type(exc).__name__ + "; check router ZTP logs"
            )
        )
        sys.exit(1)
