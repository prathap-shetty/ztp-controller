import json
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.main import create_app
from app.persistence.models import Attempt, ValidationJob
from app.services.validation import validate_once
from app.services.workflow import claim_validation, finish_validation

pytestmark = pytest.mark.postgres


@pytest.fixture
def m2a(records, settings, tmp_path):
    records["observed"]["current_version"] = "10.4(3)F"
    records["device"]["config_context"]["ztp"]["initial_configuration"] = {
        "ssh_sources": ["192.0.2.0/24"],
        "dns_servers": ["192.0.2.1"],
        "ntp_servers": ["192.0.2.1"],
    }
    profiles = json.loads(settings.catalog_path.read_text())
    profiles[0]["replay_method"] = "scheduled-config-exit"
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps(profiles))
    return settings.model_copy(
        update={
            "catalog_path": path,
            "execution_mode": "configuration-only",
            "allow_unqualified_lab": True,
            "ssh_public_key_file": Path("tests/fixtures/validation.pub"),
            "validation_delay_seconds": 1,
        }
    )


def make_due(db):
    with db.begin() as connection:
        connection.execute(update(ValidationJob).values(available_at=0))


def register(client, records):
    response = client.post("/api/v1/ztp/register", json=records["observed"])
    assert response.status_code == 200, response.text
    result = response.json()
    return result, {"Authorization": "Bearer " + result["status_token"]}


def test_config_grants_callbacks_validation_and_no_activation(records, netbox, m2a, db):
    with TestClient(create_app(m2a, netbox[0], db)) as client:
        result, headers = register(client, records)
        config_path = "/api/v1/ztp/config/" + result["provisioning_id"]
        assert client.get(config_path).status_code == 401
        config = client.get(config_path, headers=headers)
        assert config.status_code == 200
        assert config.headers["x-content-sha256"] == result["manifest"]["config"]["sha256"]
        assert "sshkey ssh-rsa" in config.text
        event = {
            "event_id": str(uuid.uuid4()),
            "event": "CONFIG_STAGED",
            "config_sha256": result["manifest"]["config"]["sha256"],
        }
        path = "/api/v1/ztp/status/" + result["provisioning_id"]
        assert client.post(path, json=event, headers=headers).json()["state"] == "CONFIGURING"
        assert client.post(path, json=event, headers=headers).json()["duplicate"] is True
        assert (
            client.post(path, json={**event, "event": "VALIDATED"}, headers=headers).status_code
            == 422
        )
        validator = Mock()
        validator.validate.return_value = ("valid", {"reason": "verified"})
        make_due(db)
        assert validate_once(db, netbox[0], validator, m2a)
        assert client.get(path, headers=headers).json()["state"] == "VALIDATED"
        assert client.get(config_path, headers=headers).status_code == 409
        assert client.post("/api/v1/ztp/register", json=records["observed"]).status_code == 409
        assert records["device"]["status"]["value"] == "staged"
    assert all(c.method == "GET" for c in netbox[1])


def test_lost_callback_still_validates_and_job_survives_restart(records, netbox, m2a, db):
    with TestClient(create_app(m2a, netbox[0], db)) as client:
        result, headers = register(client, records)
    with TestClient(create_app(m2a, netbox[0], db)) as restarted:
        again, _ = register(restarted, records)
        assert result["provisioning_id"] == again["provisioning_id"]
        with db.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(ValidationJob)) == 1
        make_due(db)
        validator = Mock()
        validator.validate.return_value = ("valid", {"reason": "verified"})
        validate_once(db, netbox[0], validator, m2a)
        assert (
            restarted.get(
                "/api/v1/ztp/status/" + result["provisioning_id"], headers=headers
            ).json()["state"]
            == "VALIDATED"
        )


@pytest.mark.parametrize("kind", ["revoked", "drift", "expired", "failed"])
def test_artifact_revocation(records, netbox, m2a, db, kind):
    with TestClient(create_app(m2a, netbox[0], db)) as client:
        result, headers = register(client, records)
        if kind == "revoked":
            records["device"]["config_context"]["provisioning"]["ztp_enabled"] = False
        elif kind == "drift":
            records["device"]["name"] = "different"
        elif kind == "expired":
            from app.persistence.models import StatusGrant

            with db.begin() as connection:
                connection.execute(update(StatusGrant).values(expires_at=0))
        else:
            event = {
                "event_id": str(uuid.uuid4()),
                "event": "FAILED",
                "config_sha256": result["manifest"]["config"]["sha256"],
            }
            assert (
                client.post(
                    "/api/v1/ztp/status/" + result["provisioning_id"], json=event, headers=headers
                ).status_code
                == 200
            )
        assert client.get(
            "/api/v1/ztp/config/" + result["provisioning_id"], headers=headers
        ).status_code in {401, 403, 409}


def test_lease_fencing_and_bounded_validation(records, netbox, m2a, db):
    with TestClient(create_app(m2a, netbox[0], db)) as client:
        register(client, records)
    make_due(db)
    old = claim_validation(db, 180)
    assert claim_validation(db, 180) is None
    with db.begin() as connection:
        connection.execute(update(ValidationJob).values(lease_until=0))
    fresh = claim_validation(db, 180)
    assert not finish_validation(db, old, "valid", {"reason": "verified"}, m2a)
    assert finish_validation(db, fresh, "retry", {"reason": "ssh_unavailable"}, m2a)
    with db.begin() as connection:
        connection.execute(
            update(ValidationJob).values(deadline=int(time.time()) - 1, available_at=0)
        )
    validator = Mock()
    validate_once(db, netbox[0], validator, m2a)
    validator.validate.assert_not_called()
    with db.connect() as connection:
        assert connection.scalar(select(Attempt.state)) == "FAILED"
        assert connection.scalar(select(ValidationJob.done)) is True
    assert records["device"]["status"]["value"] == "staged"


def test_configuration_intent_drift_prevents_ssh(records, netbox, m2a, db):
    with TestClient(create_app(m2a, netbox[0], db)) as client:
        register(client, records)
    records["device"]["config_context"]["ztp"]["initial_configuration"]["dns_servers"] = [
        "192.0.2.3"
    ]
    make_due(db)
    validator = Mock()
    validate_once(db, netbox[0], validator, m2a)
    validator.validate.assert_not_called()
    with db.connect() as connection:
        assert connection.scalar(select(Attempt.failure_reason)) == "intent_changed"
