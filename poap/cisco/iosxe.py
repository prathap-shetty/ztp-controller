#!/usr/bin/env python3
"""ZTP Lite IOS XE configuration-only bootstrap; runs in Guest Shell with the cli module."""

import hashlib
import json
import os
import re
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
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


class ControllerUnreachable(BootstrapError):
    """No HTTP response: refused, unroutable or timed out. Safe to retry."""


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
    except URLError as exc:
        # The reason is a local socket error such as "Network is unreachable".
        raise ControllerUnreachable("Controller unreachable: " + str(exc.reason)) from None


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


class FileCheckpoint:
    """Checkpoint file in a flash directory Guest Shell can write."""

    def __init__(self, directory, attempt_id, digest):
        self.path = os.path.join(directory, attempt_id + ".json")
        self.digest = digest

    def state(self):
        if not os.path.exists(self.path):
            return None
        with open(self.path) as handle:
            saved = json.load(handle)
        return saved.get("state") if saved.get("sha256") == self.digest else "mismatch"

    def mark(self, state):
        save_checkpoint(self.path, self.digest, state)


class IosCheckpoint:
    """Checkpoint markers written by IOS when Guest Shell cannot write flash.

    Day0 Guest Shell on some releases (for example Catalyst 8000V 17.4) cannot
    write /flash or /bootflash, while IOS can. The state is encoded in the file
    name; the content is only the output of "show clock", never configuration.
    """

    def __init__(self, cli, attempt_id, digest):
        self.cli = cli
        self.base = "ztp-lite-" + attempt_id + "-" + digest[:12]

    def state(self):
        listing = command(self.cli, "dir bootflash:")
        names = set(re.findall(r"(" + re.escape(self.base) + r"\.(?:applying|committed))", listing))
        if self.base + ".committed" in names:
            return "committed"
        if self.base + ".applying" in names:
            return "applying"
        # Markers for this attempt with another digest mean an uncertain outcome.
        if re.search(re.escape(self.base[:-12]) + r"[0-9a-f]{12}\.", listing):
            return "mismatch"
        return None

    def mark(self, state):
        command(self.cli, "show clock | redirect bootflash:" + self.base + "." + state)
        # Read back through IOS: a redirect that silently failed must not count.
        if self.state() != state:
            raise BootstrapError("Could not record the " + state + " checkpoint on bootflash")


def open_checkpoint(cli, attempt_id, digest):
    try:
        return FileCheckpoint(checkpoint_directory(), attempt_id, digest)
    except BootstrapError:
        return IosCheckpoint(cli, attempt_id, digest)


def checkpoint_directory():
    # Mounts and permissions differ between Guest Shell variants; use the first
    # writable one. The order is fixed, so a rerun finds the same checkpoint.
    problems = []
    for root in CHECKPOINT_ROOTS:
        if not os.path.isdir(root):
            continue
        directory = os.path.join(root, "ztp-lite")
        try:
            os.makedirs(directory, mode=0o700, exist_ok=True)
        except OSError as exc:
            problems.append(root + ": " + (exc.strerror or type(exc).__name__))
            continue
        if os.access(directory, os.W_OK):
            return directory
        problems.append(directory + ": not writable")
    raise BootstrapError(
        "No writable flash directory for the apply checkpoint ("
        + ("; ".join(problems) or "no flash mount found")
        + ")"
    )


SECRET_WORDS = re.compile(r"\b(?:password|secret|key-string)\b", re.I)


def shown(text):
    """Operator-safe excerpt: never print a line that may carry a credential."""
    text = " ".join(str(text or "").split())
    return "<secret line>" if SECRET_WORDS.search(text) else text[:120]


IOS_ERROR = re.compile(r"%\s*(?:Invalid|Incomplete|Ambiguous|Unknown|Error)", re.I)


def config_paths(text):
    """Commands with their parent context, from indentation."""
    result, parents = set(), []
    for line in text.splitlines():
        stripped = " ".join(line.split())
        if not stripped or stripped.startswith(("!", "#")):
            continue
        indent = len(line) - len(line.lstrip())
        while parents and parents[-1][0] >= indent:
            parents.pop()
        path = tuple(p[1] for p in parents) + (stripped,)
        result.add(path)
        parents.append((indent, stripped))
    return result


def missing_from_running(expected, running):
    """Rendered lines absent from running config, as safe descriptions.

    Secret lines are stored hashed and exec-style lines are not shown, so they
    are skipped; "no shutdown" is the default and only fails if "shutdown" shows.
    """
    present = config_paths(running)
    missing = []
    for path in sorted(config_paths(expected)):
        last = path[-1]
        if last == "end" or last.startswith("crypto key generate ") or SECRET_WORDS.search(last):
            continue
        if last == "no shutdown":
            if path[:-1] + ("shutdown",) in present:
                missing.append(" > ".join(path[:-1] + ("shutdown present",)))
            continue
        if path not in present:
            missing.append(shown(" > ".join(path)))
    return missing


def configure(cli, config):
    """Apply line by line; return None on success, else a safe description.

    Each rendered line must have a successful result. Some releases (for example
    Catalyst 8000V 17.4) return results without the command text; then the
    applied lines are checked in the running configuration instead.
    """
    sent = [
        line.rstrip()
        for line in config.decode().splitlines()
        if line.strip() and not line.lstrip().startswith("!") and line.strip() != "end"
    ]
    # Compare commands without indentation or repeated spaces.
    lines = [" ".join(line.split()) for line in sent]
    try:
        results = cli.configure(sent)
    except Exception as exc:
        return "configure raised " + type(exc).__name__
    if not results:
        return "configure returned no results"
    outcome = {}
    for result in results:
        text = " ".join(str(getattr(result, "command", "") or "").split())
        output = str(getattr(result, "output", "") or getattr(result, "notes", "") or "")
        if not text:
            if IOS_ERROR.search(output):
                return "unattributed IOS error -> " + shown(output)
            continue
        if not getattr(result, "success", False) or IOS_ERROR.search(output):
            outcome.setdefault(text, output or "rejected")
        else:
            outcome.setdefault(text, None)
    # A named rejection always fails, even when other results are unattributed.
    for number, line in enumerate(lines, 1):
        if outcome.get(line) is not None:
            detail = "line " + str(number) + " rejected: " + shown(line)
            if shown(line) != "<secret line>":
                detail += " -> " + shown(outcome[line])
            return detail
    if all(line in outcome for line in lines):
        return None
    # Results do not name the commands: check what actually took effect.
    try:
        running = command(cli, "show running-config")
    except BootstrapError:
        return "results not attributable and show running-config failed"
    missing = missing_from_running(config.decode(), running)
    if missing:
        return "not in running-config after apply: " + "; ".join(missing[:3])
    return None


def save(cli):
    """Save with write memory; return None on success, else a safe description."""
    try:
        output = cli.execute("write memory")
    except Exception as exc:
        return "write memory raised " + type(exc).__name__
    if "[OK]" in output:
        return None
    return "write memory did not report [OK]: " + shown(output)


def register(observed):
    # Guest Shell networking can lag its start. Registration returns the same
    # attempt when repeated, so only a missing HTTP response is retried.
    for attempt in range(6):
        try:
            return json.loads(request("/api/v1/ztp/register", observed).decode())
        except ControllerUnreachable:
            if attempt == 5:
                raise
            time.sleep(10)


def apply(cli, observed, interface):
    registration = register(observed)
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
    digest = manifest["config"]["sha256"]
    checkpoint = open_checkpoint(cli, attempt_id, digest)
    previous = checkpoint.state()
    if previous is not None:
        if previous != "committed":
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
        checkpoint.mark("applying")
        event(registration, "CONFIG_APPLYING")
        # IOS XE applies line by line; lines before a rejected one stay in running config.
        problem = configure(cli, config) or save(cli)
        if problem:
            try:
                event(registration, "FAILED")
            except Exception:
                pass
            raise BootstrapError("XE configuration apply or save failed (" + problem + ")")
        checkpoint.mark("committed")
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
