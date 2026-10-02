import json
import os
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from app.inventory.netbox import NetBoxInventoryProvider
from app.persistence.database import build_engine
from app.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def records():
    return {
        name: json.loads((FIXTURES / f"{name}.json").read_text())
        for name in ("device", "ip", "observed")
    }


@pytest.fixture
def netbox(records):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET", "M1 must never write to NetBox"
        assert request.headers["Authorization"] == "Token test-token"
        paths = {
            "/api/dcim/devices/": {"results": [records["device"]], "next": None},
            "/api/dcim/devices/1/": records["device"],
            "/api/ipam/ip-addresses/10/": records["ip"],
        }
        return httpx.Response(200, json=paths[request.url.path])

    provider = NetBoxInventoryProvider(
        "https://netbox.test",
        "test-token",
        transport=httpx.MockTransport(handler),
    )
    yield provider, calls
    provider.close()


@pytest.fixture
def db(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database")
    # The explicit test-only variable authorizes resetting these test tables.
    engine = build_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "DROP TABLE IF EXISTS registration_failures, validation_jobs, "
                "attempt_events, status_grants, "
                "registration_buckets, "
                "attempts, alembic_version CASCADE"
            )
        )
    monkeypatch.setenv("ZTP_DATABASE_URL", url)
    command.upgrade(Config("alembic.ini"), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def settings(db):
    return Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        netbox_url="https://netbox.test",
        netbox_token="test-token",
        catalog_path=FIXTURES / "profiles.json",
    )
