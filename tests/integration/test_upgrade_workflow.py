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


@pytest.mark.parametrize("running", ["10.5(4)", "10.5(5)"])
def test_erased_device_gets_new_attempt_without_install(
    records, netbox, settings, db, tmp_path, running
):
    from pydantic import SecretStr
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.persistence.models import Attempt, StatusGrant
    from app.reprovision import prepare_reprovision

    records["device"]["config_context"]["ztp"]["target_nxos"] = "10.5(4)M"
    profiles = json.loads(settings.catalog_path.read_text())
    profiles[0].update(
        target_version="10.5(4)M",
        install_method="poap-install-no-reload",
        upgrade_path_approved=True,
        replay_method="scheduled-config-exit",
    )
    catalog = tmp_path / "profiles.json"
    catalog.write_text(json.dumps(profiles))
    settings = settings.model_copy(
        update={
            "execution_mode": "upgrade-and-configure",
            "lite_mode": True,
            "admin_password": SecretStr("TestPassword123"),
            "allow_newer_version": True,
            "skip_source_validation": True,
            "allow_unqualified_lab": True,
            "catalog_path": catalog,
        }
    )
    with TestClient(create_app(settings, netbox[0], db)) as client:
        first = client.post("/api/v1/ztp/register", json=records["observed"])
        assert first.status_code == 200, first.text
        old = first.json()
        serial = records["observed"]["serial_number"]
        prepare_reprovision(db, serial)
        new = client.post(
            "/api/v1/ztp/register", json={**records["observed"], "current_version": running}
        )
        assert new.status_code == 200, new.text
        assert new.json()["provisioning_id"] != old["provisioning_id"]
        assert new.json()["manifest"]["actions"] == ["stage-config"]
        assert not new.json()["manifest"]["upgrade_required"]
        with Session(db) as session:
            previous = session.get(Attempt, old["provisioning_id"])
            assert previous.failure_reason == "operator_reprovision"
            assert previous.observed["serial_number"] == serial
            assert not session.scalars(
                select(StatusGrant).where(StatusGrant.attempt_id == previous.id)
            ).all()
