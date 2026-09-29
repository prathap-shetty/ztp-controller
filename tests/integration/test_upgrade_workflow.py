import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.postgres


def test_upgrade_image_authorization_and_target_resume(records, netbox, settings, db, tmp_path):
    image = b"fixture-image-content"
    digest = hashlib.sha256(image).hexdigest()
    records["device"]["config_context"]["ztp"]["image"]["sha256"] = digest
    records["device"]["config_context"]["ztp"]["initial_configuration"] = {
        "ssh_sources": ["192.0.2.0/24"]
    }
    profiles = json.loads(settings.catalog_path.read_text())
    profiles[0].update(
        install_method="poap-install-no-reload",
        upgrade_path_approved=True,
        replay_method="scheduled-config-exit",
        image_size_bytes=len(image),
        image_checksum=digest,
    )
    catalog = tmp_path / "profiles.json"
    catalog.write_text(json.dumps(profiles))
    (tmp_path / profiles[0]["image_name"]).write_bytes(image)
    settings = settings.model_copy(
        update={
            "execution_mode": "upgrade-and-configure",
            "allow_unqualified_lab": True,
            "ssh_public_key_file": Path("tests/fixtures/validation.pub"),
            "catalog_path": catalog,
            "image_directory": tmp_path,
        }
    )
    with TestClient(create_app(settings, netbox[0], db)) as client:
        r = client.post("/api/v1/ztp/register", json=records["observed"])
        assert r.status_code == 200, r.text
        first = r.json()
        url = "/api/v1/ztp/image/" + first["provisioning_id"]
        assert first["manifest"]["upgrade_required"]
        assert client.get(url).status_code == 401
        headers = {"Authorization": "Bearer " + first["status_token"]}
        assert client.get(url, headers=headers).content == image
        # Target-image boot keeps the original attempt, plan, and deadline.
        target = {**records["observed"], "current_version": profiles[0]["target_version"]}
        resumed = client.post("/api/v1/ztp/register", json=target)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["provisioning_id"] == first["provisioning_id"]
        assert resumed.json()["plan_hash"] == first["plan_hash"]
        records["device"]["config_context"]["provisioning"]["ztp_enabled"] = False
        assert client.get(url, headers=headers).status_code == 403
