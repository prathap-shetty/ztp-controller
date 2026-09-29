import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text, update

from app.inventory.netbox import NetBoxInventoryProvider
from app.main import create_app
from app.persistence.models import Attempt, AttemptEvent, StatusGrant
from app.persistence.repository import Repository

pytestmark = pytest.mark.postgres


def test_registration_resume_restart_and_scoped_status(records, netbox, settings, db):
    app = create_app(settings, netbox[0], db)
    with TestClient(app) as client:
        assert client.get("/health").json()["execution_enabled"] is False
        assert client.get("/ready").status_code == 200
        first = client.post("/api/v1/ztp/register", json=records["observed"])
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        first = first.json()
        second = client.post("/api/v1/ztp/register", json=records["observed"]).json()
        assert first["provisioning_id"] == second["provisioning_id"]
        assert first["plan_hash"] == second["plan_hash"]
        assert first["status_token"] != second["status_token"]
        assert "must-never-be-stored" not in str(first)
        url = f"/api/v1/ztp/status/{first['provisioning_id']}"
        assert client.get(url).status_code == 401
        headers = {"Authorization": f"Bearer {first['status_token']}"}
        assert client.get(url, headers=headers).json()["state"] == "AUTHORIZED"
        assert client.post(url, json={"state": "COMPLETE"}).status_code == 422
        assert client.get("/api/v1/ztp/config/arbitrary").status_code == 422
    with TestClient(create_app(settings, netbox[0], db)) as restarted:
        assert restarted.get(url, headers=headers).status_code == 200
        third = restarted.post("/api/v1/ztp/register", json=records["observed"]).json()
        assert third["provisioning_id"] == first["provisioning_id"]
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Attempt)) == 1
        assert connection.scalar(select(func.count()).select_from(AttemptEvent)) == 1
        assert "must-never-be-stored" not in str(connection.execute(select(Attempt)).all())
        assert first["status_token"] not in str(connection.execute(select(StatusGrant)).all())
    assert all(call.method == "GET" for call in netbox[1])


def test_concurrent_registration_one_attempt(records, netbox, settings, db):
    with TestClient(create_app(settings, netbox[0], db)) as client:
        with ThreadPoolExecutor(max_workers=8) as pool:
            responses = list(
                pool.map(
                    lambda _: client.post("/api/v1/ztp/register", json=records["observed"]),
                    range(16),
                )
            )
        assert {r.status_code for r in responses} == {200}
        assert len({r.json()["provisioning_id"] for r in responses}) == 1
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Attempt)) == 1
        assert connection.scalar(select(func.count()).select_from(AttemptEvent)) == 1


@pytest.mark.parametrize("change", ["intent", "observed"])
def test_drift_conflicts_without_replacing_snapshot(records, netbox, settings, db, change):
    with TestClient(create_app(settings, netbox[0], db)) as client:
        first = client.post("/api/v1/ztp/register", json=records["observed"]).json()
        if change == "intent":
            records["device"]["name"] = "changed-name"
        else:
            records["observed"]["current_version"] = "10.4(3)F"
        assert client.post("/api/v1/ztp/register", json=records["observed"]).status_code == 409
    with db.connect() as connection:
        assert connection.scalar(select(Attempt.plan_hash)) == first["plan_hash"]


@pytest.mark.parametrize("kind", ["active", "disabled", "unknown-model", "missing-image"])
def test_denial_creates_no_attempt(records, netbox, settings, db, kind):
    if kind == "active":
        records["device"]["status"]["value"] = "active"
    elif kind == "disabled":
        records["device"]["config_context"]["provisioning"]["ztp_enabled"] = False
    elif kind == "unknown-model":
        records["device"]["device_type"]["model"] = "UNKNOWN"
    else:
        records["device"]["config_context"]["ztp"].pop("image")
    with TestClient(create_app(settings, netbox[0], db)) as client:
        response = client.post("/api/v1/ztp/register", json=records["observed"])
        assert response.status_code == 403
    with db.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Attempt)) == 0


def test_revocation_and_expired_tokens(records, netbox, settings, db):
    with TestClient(create_app(settings, netbox[0], db)) as client:
        result = client.post("/api/v1/ztp/register", json=records["observed"]).json()
        url = f"/api/v1/ztp/status/{result['provisioning_id']}"
        headers = {"Authorization": f"Bearer {result['status_token']}"}
        records["device"]["status"]["value"] = "active"
        assert client.get(url, headers=headers).status_code == 403
        records["device"]["status"]["value"] = "staged"
        with db.begin() as connection:
            connection.execute(update(StatusGrant).values(expires_at=int(time.time()) - 1))
        assert client.get(url, headers=headers).status_code == 401
        Repository(db).cleanup()
        with db.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(StatusGrant)) == 0


def test_rate_limit_and_inventory_outage(records, settings, db):
    provider = NetBoxInventoryProvider(
        "https://netbox.test",
        "token",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(503, text="secret"),
        ),
    )
    config = settings.model_copy(update={"registration_limit_per_minute": 1})
    with TestClient(create_app(config, provider, db)) as client:
        response = client.post("/api/v1/ztp/register", json=records["observed"])
        assert response.status_code == 503
        assert "secret" not in response.text
        assert client.post("/api/v1/ztp/register", json=records["observed"]).status_code == 429
    provider.close()


def test_malformed_registration(records, netbox, settings, db):
    records["observed"]["inventory_provider"] = "infrahub"
    with TestClient(create_app(settings, netbox[0], db)) as client:
        assert client.post("/api/v1/ztp/register", json=records["observed"]).status_code == 422


def test_migration_round_trip(db):
    command.downgrade(Config("alembic.ini"), "base")
    command.upgrade(Config("alembic.ini"), "head")
    with db.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
