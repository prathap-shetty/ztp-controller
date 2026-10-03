import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app

pytestmark = pytest.mark.postgres


def test_dashboard_disabled_outside_lite(settings, netbox, db):
    with TestClient(create_app(settings, netbox[0], db)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/dashboard").status_code == 404


def test_dashboard_redacts_attempt_secrets(settings, netbox, db, records):
    settings = settings.model_copy(
        update={
            "lite_mode": True,
            "dashboard_token": SecretStr("test-dashboard-token-123456"),
            "dashboard_secure_cookie": False,
        }
    )
    with TestClient(create_app(settings, netbox[0], db)) as client:
        assert client.get("/api/dashboard").status_code == 401
        assert "Access token" in client.get("/").text
        assert client.post("/api/dashboard/login", json={"token": "wrong"}).status_code == 401
        assert (
            client.post(
                "/api/dashboard/login", json={"token": "test-dashboard-token-123456"}
            ).status_code
            == 200
        )
        registration = client.post("/api/v1/ztp/register", json=records["observed"])
        assert registration.status_code == 200
        response = client.get("/api/dashboard")
        assert response.status_code == 200
        row = response.json()["attempts"][0]
        assert row["serial"] == records["observed"]["serial_number"]
        assert row["events"][0]["kind"] == "AUTHORIZED"
        assert registration.json()["status_token"] not in response.text
        assert "must-never-be-stored-or-returned" not in response.text
        assert "config_body" not in response.text
        assert client.get("/").status_code == 200
        client.post("/api/dashboard/logout")
        assert client.get("/api/dashboard").status_code == 401


def test_dashboard_local_entries_and_invalid_inventory(settings, db, tmp_path):
    from pathlib import Path

    from app.inventory.local_yaml import LocalYamlInventoryProvider

    inventory = tmp_path / "devices.yaml"
    inventory.write_text(Path("inventory/devices.example.yaml").read_text())
    settings = settings.model_copy(
        update={
            "lite_mode": True,
            "inventory_provider": "local-yaml",
            "dashboard_token": SecretStr("test-dashboard-token-123456"),
            "dashboard_secure_cookie": False,
        }
    )
    with TestClient(create_app(settings, LocalYamlInventoryProvider(inventory), db)) as client:
        client.post("/api/dashboard/login", json={"token": "test-dashboard-token-123456"})
        data = client.get("/api/dashboard").json()
        assert len(data["inventory"]) == 1
        assert data["inventory"][0]["enabled"] is False
        assert data["attempts"] == []
        inventory.write_text("broken: [")
        data = client.get("/api/dashboard").json()
        assert data["inventory_error"]
        assert data["inventory"] == []
