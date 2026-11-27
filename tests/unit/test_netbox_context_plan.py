import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from app.errors import ZtpError
from app.models.contracts import ObservedDevice
from app.vendors.netbox_nxos import NetBoxNxosAdapter


def test_context_plan_has_no_catalog_dependency(netbox, records, tmp_path):
    content = b"lab-image-fixture"
    image = tmp_path / "lab-nxos.bin"
    image.write_bytes(content)
    records["device"]["config_context"]["ztp"]["image"]["sha256"] = hashlib.sha256(
        content
    ).hexdigest()
    records["device"]["config_context"]["ztp"]["target_nxos"] = "10.5(4)"
    adapter = NetBoxNxosAdapter(tmp_path, skip_source_validation=True, allow_newer_version=True)
    intent = netbox[0].get_device_intent("1")
    observed = ObservedDevice(**records["observed"])
    plan = adapter.build_manifest(intent, observed)
    assert plan.compatibility_profile.image_size_bytes == len(content)
    assert plan.compatibility_profile.model == intent.device.model
    assert plan.target == intent.software
    assert plan.upgrade_required
    image.write_bytes(b"")
    with pytest.raises(ZtpError, match="non-empty"):
        adapter.build_manifest(intent, observed)
    image.unlink()
    image.symlink_to(Path(__file__).resolve())
    with pytest.raises(ZtpError, match="non-empty"):
        adapter.build_manifest(intent, observed)


def test_source_checks_not_silently_bypassed(netbox, records, tmp_path):
    adapter = NetBoxNxosAdapter(tmp_path)
    with pytest.raises(ZtpError, match="source bypass"):
        adapter.build_manifest(
            netbox[0].get_device_intent("1"), ObservedDevice(**records["observed"])
        )


def test_prepare_rejects_empty_image_before_writing_catalog(tmp_path):
    image = tmp_path / "nxos.bin"
    image.touch()
    catalog = tmp_path / "catalog.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/prepare_lite.py",
            "--image",
            str(image),
            "--sha256",
            hashlib.sha256(b"").hexdigest(),
            "--model",
            "N9K-C9300V",
            "--source",
            "10.4(4)",
            "--catalog",
            str(catalog),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "non-empty" in result.stderr
    assert not catalog.exists()


def test_infrahub_context_plan_uses_its_own_profile_label(netbox, records, tmp_path):
    image = tmp_path / records["device"]["config_context"]["ztp"]["image"]["filename"]
    image.write_bytes(b"image")
    adapter = NetBoxNxosAdapter(tmp_path, allow_newer_version=True, source="infrahub")
    intent = netbox[0].get_device_intent("1")
    observed = ObservedDevice(
        **{**records["observed"], "current_version": intent.software.target_version}
    )
    manifest = adapter.build_manifest(intent, observed)
    assert manifest.compatibility_profile.id == "infrahub-1"
    assert NetBoxNxosAdapter(tmp_path).source == "netbox"
    image.unlink()
    with pytest.raises(ZtpError, match="InfraHub image must exist"):
        adapter.build_manifest(intent, observed)
