import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize(
    "fault,code,stage",
    [
        ("platform", "platform_missing", "inventory_lookup"),
        ("status", "device_not_staged", "identity_validation"),
        ("checksum", "invalid_image_checksum", "intent_lookup"),
        ("context", "missing_ztp_field", "intent_lookup"),
        ("disabled", "ztp_disabled", "intent_lookup"),
        ("catalog", "catalog_mismatch", "catalog_matching"),
    ],
)
def test_registration_failure_visible_without_attempt(
    settings, records, netbox, db, fault, code, stage
):
    d = records["device"]
    if fault == "platform":
        d["platform"] = None
    if fault == "status":
        d["status"]["value"] = "active"
    if fault == "checksum":
        d["config_context"]["ztp"]["image"]["sha256"] += " "
    if fault == "context":
        d["config_context"] = {}
    if fault == "disabled":
        d["config_context"]["provisioning"]["ztp_enabled"] = False
    if fault == "catalog":
        d["config_context"]["ztp"]["image"]["filename"] = "different.bin"
    settings = settings.model_copy(
        update={
            "dashboard_token": SecretStr("operator-test-token-123456"),
            "dashboard_secure_cookie": False,
        }
    )
    with TestClient(create_app(settings, netbox[0], db)) as client:
        response = client.post("/api/v1/ztp/register", json=records["observed"])
        assert response.status_code == 403
        assert response.json()["error"]["code"] == code
        assert client.get("/api/dashboard").status_code == 401
        client.post("/api/dashboard/login", json={"token": "operator-test-token-123456"})
        data = client.get("/api/dashboard").json()
        assert data["attempts"] == []
        failure = data["failures"][0]
        assert failure["serial"] == records["observed"]["serial_number"]
        assert failure["code"] == code
        assert failure["stage"] == stage
        assert "must-never-be-stored-or-returned" not in str(data)
        assert "test-token" not in str(data)
