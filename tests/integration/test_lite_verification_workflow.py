from pathlib import Path
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.inventory.local_yaml import LocalYamlInventoryProvider
from app.main import create_app
from app.persistence.models import Attempt, AttemptEvent, ValidationJob
from app.services.validation import validate_once

pytestmark = pytest.mark.postgres

TOKEN = "test-dashboard-token-for-verify"
OBSERVED = {
    "serial_number": "REPLACE_WITH_SERIAL",
    "model": "VEOS-LAB",
    "vendor": "arista",
    "platform": "eos",
    "current_version": "4.30.7M",
    "management_mac": "52:54:00:77:88:99",
}


class Scripted:
    def __init__(self):
        self.results, self.claims = [], []

    def validate(self, claim):
        self.claims.append(claim)
        return self.results.pop(0)


def due(db):
    with Session(db) as session, session.begin():
        session.execute(update(ValidationJob).values(available_at=0))


def state(db):
    with Session(db) as session:
        attempt = session.scalar(select(Attempt))
        job = session.scalar(select(ValidationJob))
        kinds = [
            e.kind for e in session.scalars(select(AttemptEvent).order_by(AttemptEvent.created_at))
        ]
        return attempt.state, job, kinds


def test_lite_verification_is_opt_in_observational_and_retryable(settings, db, tmp_path):
    payload = yaml.safe_load(Path("inventory/devices.eos.example.yaml").read_text())
    payload["devices"][0]["ztp_enabled"] = True
    path = tmp_path / "devices.yaml"
    path.write_text(yaml.safe_dump(payload))
    provider = LocalYamlInventoryProvider(path)
    settings = settings.model_copy(
        update={
            "eos_enabled": True,
            "lite_mode": True,
            "lite_verification": True,
            "execution_mode": "upgrade-and-configure",
            "admin_password": SecretStr("TestPassword123"),
            "dashboard_token": SecretStr(TOKEN),
            "dashboard_secure_cookie": False,
        }
    )
    validator = Scripted()
    with TestClient(create_app(settings, provider, db)) as client:
        registered = client.post("/api/v1/ztp/register", json=OBSERVED).json()
        attempt, manifest = registered["provisioning_id"], registered["manifest"]
        assert manifest["validation_policy"] == "manual"  # Device-facing plan is unchanged.
        assert state(db)[1] is not None

        # Before the device reports configuration, the worker waits without checking.
        due(db)
        assert validate_once(db, provider, validator, settings)
        current, job, _ = state(db)
        assert current == "AUTHORIZED" and job.evidence["reason"] == "awaiting_device_report"
        assert validator.claims == []

        headers = {"Authorization": "Bearer " + registered["status_token"]}
        for kind in ["CONFIG_APPLYING", "CONFIG_COMMITTED"]:
            event = {
                "event_id": str(uuid4()),
                "event": kind,
                "config_sha256": manifest["config"]["sha256"],
            }
            client.post("/api/v1/ztp/status/" + attempt, headers=headers, json=event)

        validator.results.append(
            ("retry", {"reason": "ping_failed", "host_keys": ["ssh-ed25519 AAAA"]})
        )
        due(db)
        validate_once(db, provider, validator, settings)
        assert state(db)[0] == "VALIDATING"

        # A fatal Lite result never fails the attempt; the pinned key is retained.
        validator.results.append(("fatal", {"reason": "host_key_changed"}))
        due(db)
        validate_once(db, provider, validator, settings)
        current, job, kinds = state(db)
        assert current == "CONFIGURING" and job.done and kinds[-1] == "VERIFICATION_FAILED"
        assert job.evidence["host_keys"] == ["ssh-ed25519 AAAA"]
        assert validator.claims[-1]["evidence"]["host_keys"] == ["ssh-ed25519 AAAA"]

        verify = "/api/dashboard/attempts/" + attempt + "/verify"
        assert client.post(verify, headers={"X-ZTP-Action": "verify"}).status_code == 401
        client.post("/api/dashboard/login", json={"token": TOKEN})
        dashboard = client.get("/api/dashboard").json()
        row = dashboard["attempts"][0]
        assert dashboard["can_verify"] and row["verification"]["reason"] == "host_key_changed"
        assert "host_keys" not in row["verification"]
        assert client.post(verify).status_code == 403
        assert client.post(verify, headers={"X-ZTP-Action": "verify"}).status_code == 200
        current, job, _ = state(db)
        assert not job.done and job.attempts == 0

        validator.results.append(("valid", {"reason": "verified"}))
        assert validate_once(db, provider, validator, settings)
        current, job, kinds = state(db)
        assert current == "VALIDATED" and kinds[-1] == "VALIDATED"
        assert client.post(verify, headers={"X-ZTP-Action": "verify"}).status_code == 409

    disabled = settings.model_copy(update={"lite_verification": False})
    assert not validate_once(db, provider, validator, disabled)
    with TestClient(create_app(disabled, provider, db)) as client:
        client.post("/api/dashboard/login", json={"token": TOKEN})
        response = client.post(
            "/api/dashboard/attempts/" + attempt + "/verify", headers={"X-ZTP-Action": "verify"}
        )
        assert response.status_code == 403
