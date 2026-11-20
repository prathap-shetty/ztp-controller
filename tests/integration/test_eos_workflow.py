from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.main import create_app
from app.persistence.models import ValidationJob

pytestmark = pytest.mark.postgres


def test_eos_configuration_only_and_reset(settings, db, tmp_path):
    payload = yaml.safe_load(Path("inventory/devices.eos.example.yaml").read_text())
    payload["devices"][0]["ztp_enabled"] = True
    path = tmp_path / "devices.yaml"
    path.write_text(yaml.safe_dump(payload))
    provider = LocalYamlInventoryProvider(path)
    settings = settings.model_copy(
        update={
            "eos_enabled": True,
            "lite_mode": True,
            # A mixed fleet can retain NX-OS upgrades; EOS still gets config-only.
            "execution_mode": "upgrade-and-configure",
            "admin_password": SecretStr("TestPassword123"),
            "dashboard_token": SecretStr("test-dashboard-token-for-eos"),
            "dashboard_secure_cookie": False,
        }
    )
    observed = {
        "serial_number": "REPLACE_WITH_SERIAL",
        "model": "VEOS-LAB",
        "vendor": "arista",
        "platform": "eos",
        "current_version": "4.32.1F",
        "management_mac": "52:54:00:77:88:99",
    }
    with TestClient(create_app(settings, provider, db)) as client:
        response = client.post("/api/v1/ztp/register", json=observed)
        assert response.status_code == 200, response.text
        registered = response.json()
        attempt = registered["provisioning_id"]
        manifest = registered["manifest"]
        assert manifest["mode"] == "configuration-only"
        assert manifest["actions"] == ["apply-config"]
        assert "target" not in manifest and "compatibility_profile" not in manifest
        headers = {"Authorization": "Bearer " + registered["status_token"]}
        config = client.get("/api/v1/ztp/config/" + attempt, headers=headers)
        assert config.status_code == 200
        assert "ip address 10.10.10.120/24" in config.text
        assert client.get("/api/v1/ztp/image/" + attempt, headers=headers).status_code == 409
        event = {
            "event_id": str(uuid4()),
            "event": "IMAGE_VERIFIED",
            "config_sha256": manifest["config"]["sha256"],
        }
        assert (
            client.post("/api/v1/ztp/status/" + attempt, headers=headers, json=event).status_code
            == 409
        )
        for kind in ["CONFIG_APPLYING", "CONFIG_COMMITTED"]:
            event.update(event_id=str(uuid4()), event=kind)
            assert (
                client.post(
                    "/api/v1/ztp/status/" + attempt, headers=headers, json=event
                ).status_code
                == 200
            )
        status = client.get("/api/v1/ztp/status/" + attempt, headers=headers).json()
        assert status["state"] == "CONFIGURING" and status["validation_policy"] == "manual"
        with Session(db) as session:
            assert session.scalar(select(ValidationJob)) is None
        client.post("/api/dashboard/login", json={"token": "test-dashboard-token-for-eos"})
        dashboard = client.get("/api/dashboard").json()
        assert dashboard["attempts"][0]["platform"] == "eos"
        assert dashboard["inventory"][0]["target"] == "Configuration only"
        assert "TestPassword123" not in str(dashboard)
        assert (
            client.post(
                "/api/dashboard/attempts/" + attempt + "/reprovision",
                headers={"X-ZTP-Action": "reprovision"},
                json={
                    "serial": observed["serial_number"],
                    "confirm_erased": True,
                },
            ).status_code
            == 200
        )
        assert client.get("/api/v1/ztp/config/" + attempt, headers=headers).status_code == 401
        fresh = client.post("/api/v1/ztp/register", json=observed)
        assert fresh.status_code == 200
        assert fresh.json()["provisioning_id"] != attempt
        # Current inventory revocation is still checked for issued config grants.
        payload["devices"][0]["ztp_enabled"] = False
        path.write_text(yaml.safe_dump(payload))
        assert (
            client.get(
                "/api/v1/ztp/config/" + fresh.json()["provisioning_id"],
                headers={
                    "Authorization": "Bearer " + fresh.json()["status_token"],
                },
            ).status_code
            == 403
        )


def test_eos_requires_its_own_enablement(settings, db, tmp_path):
    payload = yaml.safe_load(Path("inventory/devices.eos.example.yaml").read_text())
    payload["devices"][0]["ztp_enabled"] = True
    path = tmp_path / "devices.yaml"
    path.write_text(yaml.safe_dump(payload))
    settings = settings.model_copy(
        update={
            "xe_enabled": True,
            "eos_enabled": False,
            "lite_mode": True,
            "execution_mode": "configuration-only",
            "admin_password": SecretStr("TestPassword123"),
        }
    )
    observed = {
        "serial_number": "REPLACE_WITH_SERIAL",
        "model": "VEOS-LAB",
        "vendor": "arista",
        "platform": "eos",
        "current_version": "4.32.1F",
        "management_mac": "52:54:00:77:88:99",
    }
    with TestClient(create_app(settings, LocalYamlInventoryProvider(path), db)) as client:
        response = client.post("/api/v1/ztp/register", json=observed)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "eos_disabled"
