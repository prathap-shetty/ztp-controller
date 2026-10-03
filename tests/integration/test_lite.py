import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.persistence.models import ValidationJob
from app.settings import Settings

pytestmark = pytest.mark.postgres


def test_lite_no_keys_no_validation_jobs(records, netbox, settings, db, tmp_path):
    profiles = json.loads(settings.catalog_path.read_text())
    profiles[0].update(
        install_method="poap-install-no-reload",
        upgrade_path_approved=True,
        replay_method="scheduled-config-exit",
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(profiles))
    config = Settings(
        database_url=settings.database_url,
        netbox_url="https://netbox.test",
        netbox_token="test-token",
        lite_mode=True,
        skip_source_validation=True,
        admin_password="LabPassword123",
        execution_mode="upgrade-and-configure",
        allow_unqualified_lab=True,
        catalog_path=catalog,
    )
    records["observed"]["current_version"] = "9.9(99)"
    with TestClient(create_app(config, netbox[0], db)) as client:
        r = client.post("/api/v1/ztp/register", json=records["observed"])
        assert r.status_code == 200, r.text
        registration = r.json()
        assert registration["manifest"]["source_validation_enabled"] is False
        assert registration["manifest"]["validation_policy"] == "manual"
        aid = registration["provisioning_id"]
        headers = {"Authorization": "Bearer " + registration["status_token"]}
        content = client.get("/api/v1/ztp/config/" + aid, headers=headers).text
        assert "username admin password 0 LabPassword123 role network-admin" in content
        assert "sshkey" not in content
        r = client.post(
            "/api/v1/ztp/status/" + aid,
            headers=headers,
            json={
                "event_id": str(uuid.uuid4()),
                "event": "CONFIG_STAGED",
                "config_sha256": registration["manifest"]["config"]["sha256"],
            },
        )
        assert r.status_code == 200
        status = client.get("/api/v1/ztp/status/" + aid, headers=headers).json()
        assert status["state"] == "CONFIGURING"
        assert status["validation_policy"] == "manual"
        assert status["validation"] is None
        with db.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(ValidationJob)) == 0
