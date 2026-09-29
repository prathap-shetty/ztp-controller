import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.errors import ZtpError
from app.models.contracts import InitialConfiguration, ObservedDevice
from app.services.rendering import configuration_manifest
from app.services.validation import SshValidator, configuration_matches
from app.settings import Settings
from app.vendors.cisco_nxos import CiscoNxosAdapter
from scripts.kea_profiles import configuration, secure_options
from scripts.release_bootstrap import release


@pytest.fixture
def config_setup(netbox, records, tmp_path):
    intent = (
        netbox[0]
        .get_device_intent("1")
        .model_copy(
            update={
                "initial_configuration": InitialConfiguration(
                    ssh_sources=["192.0.2.0/24"],
                    dns_servers=["192.0.2.1"],
                    ntp_servers=["192.0.2.1"],
                ),
            }
        )
    )
    observed = ObservedDevice(**{**records["observed"], "current_version": "10.4(3)F"})
    profiles = json.loads(Path("tests/fixtures/profiles.json").read_text())
    profiles[0]["replay_method"] = "scheduled-config-exit"
    catalog = tmp_path / "profiles.json"
    catalog.write_text(json.dumps(profiles))
    settings = Settings(
        database_url="postgresql+psycopg://unused",
        netbox_url="https://netbox.test",
        netbox_token="test-token",
        catalog_path=catalog,
        execution_mode="configuration-only",
        allow_unqualified_lab=True,
        ssh_public_key_file=Path("tests/fixtures/validation.pub"),
    )
    plan = CiscoNxosAdapter(catalog).build_manifest(intent, observed)
    manifest, body = configuration_manifest(plan, intent, settings)
    return settings, intent, observed, manifest, body


def test_rendered_configuration_and_hash(config_setup):
    settings, intent, observed, manifest, body = config_setup
    assert manifest.mode == "configuration-only" and manifest.actions == ("stage-config",)
    assert "ip address 192.0.2.10/24" in body
    assert "ip access-class ZTP-SSH in" in body
    assert manifest.config.sha256 == hashlib.sha256(body.encode()).hexdigest()
    assert (
        configuration_manifest(
            CiscoNxosAdapter(settings.catalog_path).build_manifest(intent, observed),
            intent,
            settings,
        )[1]
        == body
    )
    assert configuration_matches(body, body)
    assert not configuration_matches(
        body, body.replace("ip address 192.0.2.10/24", "ip address 192.0.2.11/24")
    )
    assert not configuration_matches(body, body.replace("  no shutdown", "  shutdown"))


def test_unqualified_and_wrong_version_fail(config_setup):
    settings, intent, observed, _, _ = config_setup
    adapter = CiscoNxosAdapter(settings.catalog_path)
    with pytest.raises(ZtpError, match="qualification"):
        configuration_manifest(
            adapter.build_manifest(intent, observed),
            intent,
            settings.model_copy(update={"allow_unqualified_lab": False}),
        )
    old = observed.model_copy(update={"current_version": "10.3(5)"})
    with pytest.raises(ZtpError, match="target version"):
        configuration_manifest(adapter.build_manifest(intent, old), intent, settings)


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("bootstrap", "poap/cisco/poap.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bootstrap_schedules_once_and_never_installs(config_setup, tmp_path):
    _, _, observed, manifest, body = config_setup
    module = load_bootstrap()
    import uuid

    attempt = str(uuid.uuid4())
    calls = []

    def cli(command):
        calls.append(command)
        if command == "show version | json":
            return json.dumps({"nxos_ver_str": observed.current_version})
        if command == "show inventory | json":
            return json.dumps(
                {
                    "TABLE_inv": {
                        "ROW_inv": {
                            "name": "Chassis",
                            "serialnum": observed.serial_number,
                            "productid": observed.model,
                        }
                    }
                }
            )
        assert command == "copy bootflash:ztp-" + attempt + ".cfg scheduled-config"
        return "Copy complete."

    def api(path, data=None, token=None):
        if path.endswith("register"):
            return json.dumps(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "provisioning_id": attempt,
                    "status_token": "test-token",
                    "state": "AUTHORIZED",
                }
            ).encode()
        assert token == "test-token"
        if "/config/" in path:
            return body.encode()
        raise RuntimeError("Lost callback")

    environ = {"POAP_VRF": "management", "POAP_MAC": "001122334455"}
    assert module.run(cli, environ, str(tmp_path), api) == 0
    assert module.run(cli, environ, str(tmp_path), api) == 0
    assert sum(c.startswith("copy ") for c in calls) == 1
    assert not any("install" in c or "reload" in c for c in calls)
    assert (tmp_path / ("ztp-" + attempt + ".cfg")).read_text() == body


def test_release_embedded_checksum(tmp_path):
    release(Path("poap/cisco/poap.py"), tmp_path, "https://192.0.2.2")
    script = (tmp_path / "poap.py").read_bytes()
    without = b"".join(
        line for line in script.splitlines(keepends=True) if not line.startswith(b"#md5sum=")
    )
    assert hashlib.md5(without, usedforsecurity=False).hexdigest().encode() in script
    assert b'CONTROLLER_URL = "https://192.0.2.2"' in script
    assert b"GENERATED_BY_RELEASE_TOOL" not in script
    with pytest.raises(ValueError):
        release(Path("poap/cisco/poap.py"), tmp_path, "http://192.0.2.2")


def test_secure_dhcp_tlv_uses_two_byte_lengths():
    data = bytes.fromhex(secure_options("https://192.0.2.2/bootstrap/poap.py").replace(":", ""))
    assert data[:4] == bytes([1, 0, 1, 1])
    assert data[4] == 2
    assert int.from_bytes(data[5:7], "big") == len(data[7:])


def test_ssh_validation_uses_strict_keys_and_saved_config(config_setup):
    settings, intent, observed, manifest, body = config_setup
    settings = settings.model_copy(
        update={
            "ssh_private_key_file": Path("/keys/id_rsa"),
            "ssh_known_hosts_file": Path("/keys/known_hosts"),
        }
    )

    class Responses(list):
        failed = False

    responses = Responses(
        [
            SimpleNamespace(result=json.dumps({"nxos_ver_str": observed.current_version})),
            SimpleNamespace(
                result=json.dumps(
                    {
                        "TABLE_inv": {
                            "ROW_inv": {
                                "name": "Chassis",
                                "productid": observed.model,
                                "serialnum": observed.serial_number,
                            }
                        }
                    }
                )
            ),
            SimpleNamespace(result=body),
            SimpleNamespace(result=body),
        ]
    )
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.send_commands.return_value = responses
    factory = Mock(return_value=connection)
    claim = {
        "intent": intent.model_dump(mode="json"),
        "observed": observed.model_dump(),
        "manifest": manifest.model_dump(mode="json"),
        "config_body": body,
    }
    validator = SshValidator(settings, factory)
    assert validator.validate(claim)[0] == "valid"
    assert factory.call_args.kwargs["auth_strict_key"] is True
    assert factory.call_args.kwargs["host"] == "192.0.2.10"
    responses[-1].result = body.replace("hostname lab-leaf-01", "hostname wrong")
    assert validator.validate(claim)[0] == "retry"


def test_secure_profile_disables_kea_suboption_reinterpretation():
    profile = configuration("secure-https")["Dhcp4"]
    assert profile["option-def"] == [
        {"name": "cisco-poap", "code": 43, "type": "binary", "space": "dhcp4", "encapsulate": ""}
    ]
    assert not any(
        o["name"] in {"tftp-server-name", "boot-file-name"}
        for o in profile["subnet4"][0]["option-data"]
    )


@pytest.mark.parametrize("fault", ["checksum", "native-error", "interrupted"])
def test_bootstrap_failure_never_replays_unverified_or_ambiguous_config(
    config_setup, tmp_path, fault
):
    import uuid

    _, _, observed, manifest, body = config_setup
    module = load_bootstrap()
    attempt = str(uuid.uuid4())
    copies = []

    def cli(command):
        if command == "show version | json":
            return json.dumps({"nxos_ver_str": observed.current_version})
        if command == "show inventory | json":
            return json.dumps(
                {
                    "TABLE_inv": {
                        "ROW_inv": {
                            "name": "Chassis",
                            "serialnum": observed.serial_number,
                            "productid": observed.model,
                        }
                    }
                }
            )
        copies.append(command)
        return "Error: replay scheduling rejected"

    def api(path, data=None, token=None):
        if path.endswith("register"):
            return json.dumps(
                {
                    "manifest": manifest.model_dump(mode="json"),
                    "provisioning_id": attempt,
                    "status_token": "test",
                    "state": "AUTHORIZED",
                }
            ).encode()
        return b"corrupted" if fault == "checksum" else body.encode()

    if fault == "interrupted":
        (tmp_path / ("ztp-" + attempt + ".cfg.json")).write_text(
            json.dumps({"sha256": manifest.config.sha256, "state": "scheduling"})
        )
    with pytest.raises(RuntimeError):
        module.run(cli, {"POAP_VRF": "management", "POAP_MAC": "001122334455"}, str(tmp_path), api)
    assert len(copies) == (1 if fault == "native-error" else 0)


def test_bootstrap_certificate_failure_has_no_transport_downgrade(monkeypatch):
    import ssl
    from urllib.error import URLError

    module = load_bootstrap()
    opener = Mock()
    opener.open.side_effect = URLError(ssl.SSLCertVerificationError("invalid certificate"))
    monkeypatch.setattr(module, "build_opener", lambda *args: opener)
    with pytest.raises(RuntimeError, match="transport failed"):
        module.request("/api/v1/ztp/register", {"serial_number": "test"})
    assert opener.open.call_count == 1
