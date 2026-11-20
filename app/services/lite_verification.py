"""Opt-in Lite post-provisioning verification: ping, SSH login, identity and config.

Read-only. The worker only runs show commands. The first SSH host key seen for an
attempt is pinned (trust on first use); a later different key stops verification.
"""

import base64
import hashlib
import json
import re
import socket
import struct
import subprocess
import tempfile
import time

from scrapli import Scrapli
from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliException

from app.services.validation import config_paths, nxos_facts
from app.vendors.nxos_version import version_satisfies

USERNAME = "admin"  # Lite templates create this account.
SECRET_LINE = re.compile(r"\b(?:password|secret)\b", re.I)
HOST_KEY = re.compile(r"^(ssh-(?:rsa|ed25519)|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/=]+$")
CISCO_CHASSIS = re.compile(
    r'NAME:\s*"([^"]+)"[^\n]*\n\s*PID:\s*([^,\s]+)[^\n]*?SN:\s*([^\s,]+)', re.I
)
VIRTUAL_EOS = re.compile(r"^[vc]EOS", re.I)


def icmp_ping(host: str, count: int = 3, timeout: float = 1.0) -> bool:
    """Unprivileged ICMP echo; needs net.ipv4.ping_group_range to include the worker."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP) as channel:
        channel.settimeout(timeout)
        for sequence in range(1, count + 1):
            header = struct.pack("!BBHHH", 8, 0, 0, 0, sequence)
            channel.sendto(header + b"ztp-lite-verify", (host, 0))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    data, _ = channel.recvfrom(1024)
                except TimeoutError:
                    break
                if data and data[0] >> 4 == 4:  # Some kernels include the IP header.
                    data = data[(data[0] & 0x0F) * 4 :]
                if (
                    len(data) >= 8
                    and data[0] == 0
                    and struct.unpack("!H", data[6:8])[0] == sequence
                ):
                    return True
    return False


def scan_host_keys(host: str) -> list[str]:
    result = subprocess.run(
        ["ssh-keyscan", "-T", "5", "-t", "rsa,ecdsa,ed25519", host],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    keys = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and HOST_KEY.match(parts[1] + " " + parts[2]):
            keys.add(parts[1] + " " + parts[2])
    return sorted(keys)


def fingerprint(key: str) -> str:
    digest = hashlib.sha256(base64.b64decode(key.split()[1])).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def keys_conflict(pinned: list[str], scanned: list[str]) -> bool:
    """True when the device presents a different key for any pinned key type."""
    by_type = {key.split()[0]: key for key in pinned}
    if not any(key.split()[0] in by_type for key in scanned):
        return True
    return any(by_type.get(key.split()[0], key) != key for key in scanned)


def lite_configuration_check(expected: str, actual: str) -> tuple[bool, list[str]]:
    """Return whether rendered lines are present, and the missing non-secret lines.

    Secret lines are skipped because devices store hashes; a successful password
    login is the credential check. Defaults that platforms omit from output are
    accepted unless the opposite command is present.
    """
    present = config_paths(actual)
    required = set()
    for path in config_paths(expected):
        if any(SECRET_LINE.search(part) for part in path):
            continue
        # Exec-style commands are not shown in saved configuration.
        if path[-1] == "end" or path[-1].startswith("crypto key generate "):
            continue
        required.add(path)
    defaults = {("feature ssh",): ("no feature ssh",), ("no feature telnet",): ("feature telnet",)}
    dropped = set()
    for path in sorted(required):
        if path[-1] == "no shutdown":
            opposite = path[:-1] + ("shutdown",)
        elif path in defaults:
            opposite = defaults[path]
        else:
            continue
        if opposite in present:
            return False, [" ".join(opposite)]
        dropped.add(path)
    required -= dropped
    # A block such as "management ssh" holding only a default line may be omitted.
    for path in {p[:-1] for p in dropped if len(p) > 1}:
        if not any(r[: len(path)] == path and r != path for r in required):
            required.discard(path)
    missing = sorted(" > ".join(path) for path in required - present)
    return not missing, missing[:20]


def cisco_text_facts(version_text, inventory_text, version_pattern, chassis_names):
    chassis = [
        (pid, serial)
        for name, pid, serial in CISCO_CHASSIS.findall(inventory_text)
        if name.lower() in chassis_names
    ]
    release = re.search(version_pattern, version_text)
    if len(chassis) != 1 or not release:
        raise ValueError("Cannot identify chassis and version")
    return {
        "serial_number": chassis[0][1].upper(),
        "model": chassis[0][0].upper(),
        "current_version": release.group(1),
    }


def eos_facts(version):
    model = str(version.get("modelName") or "").upper()
    serial = str(version.get("serialNumber") or "").strip()
    mac = re.sub(r"[^0-9a-fA-F]", "", str(version.get("systemMacAddress") or ""))
    if not serial and VIRTUAL_EOS.match(model) and len(mac) == 12:
        serial = mac
    if not model or not serial:
        raise ValueError("Cannot identify EOS model and serial")
    return {
        "serial_number": serial.upper(),
        "model": model,
        "current_version": str(version.get("version") or ""),
    }


PLATFORMS = {
    "nxos": {
        "driver": "cisco_nxos",
        "commands": ["show version | json", "show inventory | json"],
        "facts": lambda out: nxos_facts(json.loads(out[0]), json.loads(out[1])),
        "startup": True,
    },
    "iosxe": {
        "driver": "cisco_iosxe",
        "commands": ["show version", "show inventory"],
        "facts": lambda out: cisco_text_facts(
            out[0],
            out[1],
            r"Cisco IOS[ -]XE Software,?\s+Version\s+([0-9][A-Za-z0-9.()-]*)",
            {"chassis"},
        ),
        "startup": True,
    },
    "iosxr": {
        "driver": "cisco_iosxr",
        "commands": ["show version", "show inventory"],
        "facts": lambda out: cisco_text_facts(
            out[0],
            out[1],
            r"(?:IOS XR Software,?\s+Version|Version)\s*:?\s*([0-9][A-Za-z0-9.()-]*)",
            {"chassis", "rack 0", "0"},
        ),
        # Committed XR configuration is the persistent configuration.
        "startup": False,
    },
    "eos": {
        "driver": "arista_eos",
        "commands": ["show version | json"],
        "facts": lambda out: eos_facts(json.loads(out[0])),
        "startup": True,
    },
}


class LiteSshValidator:
    def __init__(
        self, settings, connection_factory=Scrapli, ping=icmp_ping, keyscan=scan_host_keys
    ):
        self.settings, self.connection_factory = settings, connection_factory
        self.ping, self.keyscan = ping, keyscan

    def validate(self, claim) -> tuple[str, dict]:
        platform = claim["observed"]["platform"]
        spec = PLATFORMS.get(platform)
        if spec is None:
            return "fatal", {"reason": "unsupported_platform"}
        host = claim["intent"]["management"]["address"].split("/")[0]
        checks = {"ping": False, "ssh": False, "identity": False, "configuration": False}
        evidence = {"reason": "", "checks": checks, "checked_at": int(time.time())}
        pinned = list((claim.get("evidence") or {}).get("host_keys") or [])
        if pinned:
            evidence.update(host_keys=pinned, host_key=fingerprint(pinned[0]))
        try:
            checks["ping"] = self.ping(host)
        except OSError:
            return "retry", {**evidence, "reason": "ping_unavailable"}
        if not checks["ping"]:
            return "retry", {**evidence, "reason": "ping_failed"}
        try:
            scanned = self.keyscan(host)
        except (OSError, subprocess.SubprocessError):
            scanned = []
        if not scanned:
            return "retry", {**evidence, "reason": "ssh_unavailable"}
        if pinned and keys_conflict(pinned, scanned):
            return "fatal", {**evidence, "reason": "host_key_changed"}
        if not pinned:
            evidence.update(host_keys=scanned, host_key=fingerprint(scanned[0]))
        commands = spec["commands"] + ["show running-config"]
        if spec["startup"]:
            commands.append("show startup-config")
        try:
            with tempfile.NamedTemporaryFile("w", prefix="ztp-known-hosts-") as known_hosts:
                known_hosts.write("".join(f"{host} {key}\n" for key in evidence["host_keys"]))
                known_hosts.flush()
                with self.connection_factory(
                    platform=spec["driver"],
                    host=host,
                    auth_username=USERNAME,
                    auth_password=self.settings.admin_password.get_secret_value(),
                    auth_strict_key=True,
                    ssh_known_hosts_file=known_hosts.name,
                    transport="system",
                    timeout_socket=5,
                    timeout_transport=10,
                    timeout_ops=30,
                ) as connection:
                    checks["ssh"] = True
                    responses = connection.send_commands(commands, stop_on_failed=True)
        except ScrapliAuthenticationFailed:
            return "retry", {**evidence, "reason": "ssh_login_failed"}
        except (ScrapliException, OSError) as exc:
            # The system transport reports a rejected password as a connection error.
            reason = "ssh_login_failed" if "auth failed" in str(exc) else "ssh_unavailable"
            return "retry", {**evidence, "reason": reason}
        if responses.failed or len(responses) != len(commands):
            return "retry", {**evidence, "reason": "show_command_failed"}
        output = [response.result for response in responses]
        try:
            facts = spec["facts"](output)
        except (ValueError, KeyError, TypeError, AttributeError):
            return "retry", {**evidence, "reason": "unsupported_show_output"}
        evidence["facts"] = facts
        observed = claim["observed"]
        if (
            facts["serial_number"] != observed["serial_number"]
            or facts["model"] != observed["model"].upper()
        ):
            return "fatal", {**evidence, "reason": "device_identity_mismatch"}
        target = (claim["manifest"].get("target") or {}).get("target_version")
        if target:
            try:
                running_target = version_satisfies(
                    facts["current_version"], target, claim["manifest"].get("allow_newer_version")
                )
            except ValueError:
                running_target = False
            if not running_target:
                return "retry", {**evidence, "reason": "running_version_mismatch"}
        checks["identity"] = True
        running, missing = lite_configuration_check(
            claim["config_body"], output[len(spec["commands"])]
        )
        saved, saved_missing = (
            lite_configuration_check(claim["config_body"], output[-1])
            if spec["startup"]
            else (running, [])
        )
        checks["configuration"] = running and saved
        evidence.update(
            running_config_matches=running,
            startup_config_matches=saved if spec["startup"] else None,
            missing_lines=missing or saved_missing,
        )
        if not checks["configuration"]:
            return "retry", {**evidence, "reason": "configuration_mismatch"}
        return "valid", {**evidence, "reason": "verified"}
