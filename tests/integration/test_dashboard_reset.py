from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import create_app
from app.persistence.models import Attempt, AttemptEvent, StatusGrant

pytestmark = pytest.mark.postgres


def test_dashboard_reset_auth_confirmation_and_stale_attempt(settings, netbox, db, records):
    settings = settings.model_copy(
        update={
            "poc_mode": True,
            "dashboard_token": SecretStr("dashboard-token-for-reset-test"),
            "dashboard_secure_cookie": False,
        }
    )
    with TestClient(create_app(settings, netbox[0], db)) as client:
        first = client.post("/api/v1/ztp/register", json=records["observed"]).json()
        path = "/api/dashboard/attempts/" + first["provisioning_id"] + "/reprovision"
        payload = {"serial": records["observed"]["serial_number"], "confirm_erased": True}
        headers = {"X-ZTP-Action": "reprovision"}
        assert client.post(path, json=payload, headers=headers).status_code == 401
        client.post("/api/dashboard/login", json={"token": "dashboard-token-for-reset-test"})
        assert client.post(path, json=payload).status_code == 403
        assert (
            client.post(
                path, json={**payload, "confirm_erased": False}, headers=headers
            ).status_code
            == 400
        )
        assert (
            client.post(path, json={**payload, "serial": "WRONG"}, headers=headers).status_code
            == 409
        )
        stale = "/api/dashboard/attempts/" + str(uuid4()) + "/reprovision"
        assert client.post(stale, json=payload, headers=headers).status_code == 409
        assert client.post(path, json=payload, headers=headers).status_code == 200
        with Session(db) as session:
            a = session.get(Attempt, first["provisioning_id"])
            assert a.failure_reason == "operator_reprovision"
            assert not session.scalars(
                select(StatusGrant).where(StatusGrant.attempt_id == a.id)
            ).all()
            assert session.scalar(
                select(AttemptEvent).where(
                    AttemptEvent.attempt_id == a.id, AttemptEvent.kind == "OPERATOR_REPROVISION"
                )
            )
        new = client.post("/api/v1/ztp/register", json=records["observed"]).json()
        assert new["provisioning_id"] != first["provisioning_id"]
        # A stale browser must not reset the replacement attempt.
        assert client.post(path, json=payload, headers=headers).status_code == 409
        with Session(db) as session:
            assert session.get(Attempt, new["provisioning_id"]).state == "AUTHORIZED"
