import hashlib
import io
import json
import uuid
from unittest.mock import Mock

import pytest
from test_m2a import config_setup, load_bootstrap  # noqa: F401

from app.errors import ZtpError
from app.services.images import open_image
from app.services.rendering import configuration_manifest


@pytest.fixture
def upgrade(request):
    settings, intent, observed, manifest, body = request.getfixturevalue("config_setup")
    profile = manifest.compatibility_profile.model_copy(
        update={
            "install_method": "poap-install-no-reload",
            "upgrade_path_approved": True,
            "source_versions": ["10.4(4)M"],
        }
    )
    observed = observed.model_copy(update={"current_version": "10.4(4)M"})
    settings = settings.model_copy(update={"execution_mode": "upgrade-and-configure"})
    plan = manifest.model_copy(update={"compatibility_profile": profile, "upgrade_required": True})
    manifest, body = configuration_manifest(plan, intent, settings)
    return settings, observed, manifest, body


def test_upgrade_requires_explicit_approval(request):
    settings, intent, _, manifest, _ = request.getfixturevalue("config_setup")
    settings = settings.model_copy(update={"execution_mode": "upgrade-and-configure"})
    with pytest.raises(ZtpError, match="approval"):
        configuration_manifest(manifest, intent, settings)


@pytest.mark.parametrize("fault", ["none", "install-error", "interrupted"])
@pytest.mark.parametrize("skip_source", [False, True])
def test_install_order_and_no_blind_retry(upgrade, tmp_path, fault, skip_source):
    _, observed, manifest, body = upgrade
    if skip_source:
        observed = observed.model_copy(update={"current_version": "9.9(99)"})
        manifest = manifest.model_copy(update={"source_validation_enabled": False})
    module = load_bootstrap()
    attempt = str(uuid.uuid4())
    calls = []
    registration = {
        "manifest": manifest.model_dump(mode="json"),
        "state": "AUTHORIZED",
        "provisioning_id": attempt,
        "status_token": "test",
        "plan_hash": "a" * 64,
    }

    def api(path, data=None, token=None):
        if path.endswith("/register"):
            return json.dumps(registration).encode()
        if "/config/" in path:
            return body.encode()
        calls.append(data["event"])
        return b"{}"

    def cli(cmd):
        if cmd == "show version | json":
            return json.dumps({"nxos_ver_str": observed.current_version})
        if cmd == "show inventory | json":
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
        calls.append(cmd)
        return "Error: install failed" if fault == "install-error" else ""

    def download(*args):
        calls.append("download-verified")
        return "ztp-image-test.bin"

    if fault == "interrupted":
        (tmp_path / ("ztp-" + attempt + ".cfg.json")).write_text(
            json.dumps(
                {"state": "installing", "sha256": manifest.config.sha256, "plan_hash": "a" * 64}
            )
        )
    if fault != "none":
        with pytest.raises(RuntimeError):
            module.run(
                cli,
                {"POAP_VRF": "management", "POAP_MAC": "001122334455"},
                str(tmp_path),
                api,
                download,
            )
        assert not any("scheduled-config" in c for c in calls)
    else:
        module.run(
            cli,
            {"POAP_VRF": "management", "POAP_MAC": "001122334455"},
            str(tmp_path),
            api,
            download,
        )
        assert calls[0:2] == ["download-verified", "IMAGE_VERIFIED"]
        assert "install all" in calls[2] and "no-reload" in calls[2]
        assert "scheduled-config" in calls[3]
        before = list(calls)
        module.run(
            cli,
            {"POAP_VRF": "management", "POAP_MAC": "001122334455"},
            str(tmp_path),
            api,
            download,
        )
        assert calls == before


@pytest.mark.parametrize("fault", ["none", "checksum", "size"])
def test_stream_download_integrity(tmp_path, monkeypatch, fault):
    module = load_bootstrap()
    data = b"image-data" * 10000
    profile = {
        "image_name": "nxos64-cs.10.5.4.M.bin",
        "image_checksum": hashlib.sha256(data).hexdigest(),
        "image_size_bytes": len(data),
    }
    downloaded = (
        data if fault == "none" else (b"x" * len(data) if fault == "checksum" else data[:-1])
    )
    opener = Mock()
    opener.open.return_value = io.BytesIO(downloaded)
    monkeypatch.setattr(module, "transport", lambda: opener)
    if fault != "none":
        with pytest.raises(RuntimeError, match="integrity"):
            module.download_image(str(uuid.uuid4()), "test", profile, str(tmp_path))
        assert not list(tmp_path.glob("*.bin"))
        assert not list(tmp_path.glob("*.part"))
    else:
        name = module.download_image(str(uuid.uuid4()), "test", profile, str(tmp_path))
        assert name == profile["image_name"]
        assert (tmp_path / name).read_bytes() == data
        module.download_image(str(uuid.uuid4()), "test", profile, str(tmp_path))
        assert opener.open.call_count == 1


def test_image_endpoint_file_safety(tmp_path):
    p = tmp_path / "image.bin"
    p.write_bytes(b"abc")
    profile = {"image_name": p.name, "image_size_bytes": 3}
    with open_image(tmp_path, profile) as handle:
        assert handle.read() == b"abc"
    for name in ["../image.bin", "symlink.bin"]:
        if name == "symlink.bin":
            (tmp_path / name).symlink_to(p)
        with pytest.raises(ZtpError):
            open_image(tmp_path, {**profile, "image_name": name})


@pytest.mark.parametrize("reported_version", ["10.5(4)M", "10.5(4)", "10.5(5)"])
def test_target_boot_continues_install_checkpoint_without_reinstall(
    upgrade, tmp_path, reported_version
):
    _, observed, manifest, body = upgrade
    manifest = manifest.model_copy(
        update={
            "allow_newer_version": True,
            "target": manifest.target.model_copy(update={"target_version": "10.5(4)M"}),
        }
    )
    module = load_bootstrap()
    attempt = str(uuid.uuid4())
    registration = {
        "manifest": manifest.model_dump(mode="json"),
        "state": "AUTHORIZED",
        "provisioning_id": attempt,
        "status_token": "test",
        "plan_hash": "a" * 64,
    }
    (tmp_path / ("ztp-" + attempt + ".cfg.json")).write_text(
        json.dumps(
            {
                "state": "installing",
                "sha256": manifest.config.sha256,
                "plan_hash": "a" * 64,
            }
        )
    )
    calls = []

    def cli(command):
        if command == "show version | json":
            return json.dumps({"nxos_ver_str": reported_version})
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
        calls.append(command)
        return ""

    def api(path, data=None, token=None):
        if path.endswith("/register"):
            return json.dumps(registration).encode()
        return body.encode()

    download = Mock(side_effect=AssertionError("must not download on target boot"))
    assert (
        module.run(
            cli,
            {"POAP_VRF": "management", "POAP_MAC": "001122334455"},
            str(tmp_path),
            api,
            download,
        )
        == 0
    )
    assert len(calls) == 1 and "scheduled-config" in calls[0]


def test_source_bypass_keeps_model_and_image_matching(request):
    from app.errors import InventoryDenied
    from app.vendors.cisco_nxos import CiscoNxosAdapter

    settings, intent, observed, _, _ = request.getfixturevalue("config_setup")
    unknown = observed.model_copy(update={"current_version": "9.9(99)"})
    with pytest.raises(InventoryDenied):
        CiscoNxosAdapter(settings.catalog_path).build_manifest(intent, unknown)
    adapter = CiscoNxosAdapter(settings.catalog_path, skip_source_validation=True)
    manifest = adapter.build_manifest(intent, unknown)
    assert manifest.source_validation_enabled is False
    assert manifest.upgrade_required
    with pytest.raises(InventoryDenied):
        adapter.build_manifest(intent, unknown.model_copy(update={"model": "WRONG-MODEL"}))
    software = intent.software.model_copy(update={"image_checksum": "0" * 64})
    with pytest.raises(InventoryDenied):
        adapter.build_manifest(intent.model_copy(update={"software": software}), unknown)


@pytest.mark.parametrize("name", ["../image.bin", "image.bin ; reload", "/image.bin", ""])
def test_download_rejects_unsafe_image_name(tmp_path, name):
    module = load_bootstrap()
    profile = {"image_name": name, "image_checksum": "a" * 64, "image_size_bytes": 3}
    with pytest.raises(RuntimeError, match="filename"):
        module.download_image("test", "test", profile, str(tmp_path))


def test_download_preserves_conflicting_existing_image(tmp_path):
    module = load_bootstrap()
    path = tmp_path / "nxos.bin"
    path.write_bytes(b"old")
    profile = {"image_name": path.name, "image_checksum": "a" * 64, "image_size_bytes": 3}
    with pytest.raises(RuntimeError, match="no files were overwritten"):
        module.download_image("test", "test", profile, str(tmp_path))
    assert path.read_bytes() == b"old"


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("10.5(4)", "10.5(4)M", True),
        ("10.5(4)M", "10.5(4)", True),
        ("10.5(3)", "10.5(4)M", False),
        ("10.5(4)F", "10.5(4)M", False),
        ("9.3(3)", "9.3(3)M", False),
    ],
)
def test_release_alias_consistent_with_bootstrap(left, right, expected):
    from app.vendors.nxos_version import same_nxos_release

    assert same_nxos_release(left, right) is expected
    assert load_bootstrap().same_nxos_release(left, right) is expected


@pytest.mark.parametrize(
    "current,target,expected",
    [
        ("10.5(4)", "10.5(4)M", True),
        ("10.5(5)", "10.5(4)M", True),
        ("10.6(1)", "10.5(4)M", True),
        ("10.4(2)", "10.5(4)M", False),
    ],
)
def test_minimum_version_policy(current, target, expected):
    from app.vendors.nxos_version import version_satisfies

    assert version_satisfies(current, target, True) is expected
    assert load_bootstrap().version_satisfies(current, target, True) is expected
    if current == "10.5(5)":
        assert not version_satisfies(current, target, False)


def test_unknown_newer_version_is_not_downgraded():
    from app.vendors.nxos_version import version_satisfies

    with pytest.raises(ValueError):
        version_satisfies("10.5(5)X", "10.5(4)M", True)
