import json
import time

from scrapli import Scrapli
from scrapli.exceptions import ScrapliException

from app.errors import ZtpError
from app.models.contracts import ObservedDevice
from app.services.authorization import authorize_intent
from app.services.hashing import stable_hash
from app.services.workflow import claim_validation, finish_validation
from app.vendors.nxos_version import version_satisfies


def nxos_facts(version: dict, inventory: dict) -> dict:
    rows = inventory["TABLE_inv"]["ROW_inv"]
    if isinstance(rows, dict):
        rows = [rows]
    chassis = [r for r in rows if r.get("name", "").strip('"').lower() == "chassis"]
    if len(chassis) != 1:
        raise ValueError("Expected exactly one chassis")
    release = version.get("nxos_ver_str") or version.get("kickstart_ver_str")
    if not isinstance(release, str) or not release:
        raise ValueError("Missing running version")
    return {
        "serial_number": chassis[0]["serialnum"].strip().upper(),
        "model": chassis[0]["productid"].strip().upper(),
        "current_version": release.strip(),
    }


def config_paths(config: str) -> set[tuple[str, ...]]:
    """Parse indentation into command context; compare semantic required paths."""
    result, parents = set(), []
    for line in config.splitlines():
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


def configuration_matches(expected: str, actual: str) -> bool:
    required, present = config_paths(expected), config_paths(actual)
    # NX-OS may omit these defaults from saved config. Explicit opposite values fail.
    defaults = {
        ("feature ssh",): ("no feature ssh",),
        ("no feature telnet",): ("feature telnet",),
        ("interface mgmt0", "no shutdown"): ("interface mgmt0", "shutdown"),
    }
    for path, opposite in defaults.items():
        if opposite in present:
            return False
        required.discard(path)
    return required <= present


class SshValidator:
    def __init__(self, settings, connection_factory=Scrapli):
        self.settings, self.connection_factory = settings, connection_factory

    def validate(self, claim) -> tuple[str, dict]:
        settings = self.settings
        if not settings.ssh_private_key_file or not settings.ssh_known_hosts_file:
            return "fatal", {"reason": "validation_credentials_missing"}
        commands = [
            "show version | json",
            "show inventory | json",
            "show running-config",
            "show startup-config",
        ]
        try:
            with self.connection_factory(
                platform="cisco_nxos",
                host=claim["intent"]["management"]["address"].split("/")[0],
                auth_username=settings.ssh_username,
                auth_private_key=str(settings.ssh_private_key_file),
                auth_strict_key=True,
                ssh_known_hosts_file=str(settings.ssh_known_hosts_file),
                transport="system",
                timeout_socket=5,
                timeout_transport=10,
                timeout_ops=10,
            ) as connection:
                responses = connection.send_commands(commands, stop_on_failed=True)
            if responses.failed or len(responses) != len(commands):
                return "retry", {"reason": "show_command_failed"}
            facts = nxos_facts(json.loads(responses[0].result), json.loads(responses[1].result))
            observed = claim["observed"]
            if (
                facts["serial_number"] != observed["serial_number"]
                or facts["model"] != observed["model"]
            ):
                return "fatal", {"reason": "device_identity_mismatch"}
            if not version_satisfies(
                facts["current_version"],
                claim["manifest"]["target"]["target_version"],
                claim["manifest"].get("allow_newer_version", False),
            ):
                return (
                    "retry" if claim["manifest"]["mode"] == "upgrade-and-configure" else "fatal"
                ), {"reason": "running_version_mismatch"}
            running = configuration_matches(claim["config_body"], responses[2].result)
            saved = configuration_matches(claim["config_body"], responses[3].result)
            evidence = {
                "reason": "verified" if running and saved else "configuration_mismatch",
                "facts": facts,
                "running_config_matches": running,
                "startup_config_matches": saved,
                "checked_at": int(time.time()),
            }
            return ("valid" if running and saved else "retry"), evidence
        except (ScrapliException, OSError):
            return "retry", {"reason": "ssh_unavailable"}
        except (ValueError, KeyError, TypeError, AttributeError):
            return "retry", {"reason": "unsupported_show_output"}


def validate_once(engine, inventory, validator, settings):
    if settings.lite_mode or settings.execution_mode == "planning-only":
        return False
    claim = claim_validation(engine, settings.validation_lease_seconds)
    if claim is None:
        return False
    if claim["state"] in {"VALIDATED", "FAILED"}:
        result, evidence = "fatal", {"reason": "already_terminal"}
    elif int(time.time()) >= claim["deadline"]:
        result, evidence = "fatal", {"reason": "validation_deadline_exceeded"}
    else:
        try:
            intent = inventory.get_device_intent(claim["intent"]["device"]["id"])
            authorize_intent(intent, ObservedDevice.model_validate(claim["observed"]))
            if stable_hash(intent.model_dump(mode="json")) != claim["manifest"]["intent_hash"]:
                result, evidence = "fatal", {"reason": "intent_changed"}
            else:
                result, evidence = validator.validate(claim)
        except ZtpError as exc:
            result = "retry" if exc.status == 503 else "fatal"
            evidence = {
                "reason": "inventory_unavailable" if exc.status == 503 else "policy_revoked"
            }
    finish_validation(engine, claim, result, evidence, settings)
    return True
