import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from fastapi.testclient import TestClient
from test_configuration import make_due

from app.inventory.factory import build_provider
from app.main import create_app
from app.services.validation import validate_once

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("mode", ["planning-only", "configuration-only", "upgrade-and-configure"])
def test_local_workflow_without_netbox(mode, netbox, settings, db, records, tmp_path):
    entry = netbox[0].get_device_intent("1").model_dump(mode="json")
    entry["initial_configuration"] = {"ssh_sources": ["192.0.2.0/24"]}
    path = tmp_path / "devices.yaml"
    path.write_text(yaml.safe_dump({"schema_version": 1, "devices": [entry]}))
    profiles = json.loads(settings.catalog_path.read_text())
    profiles[0].update(
        replay_method="scheduled-config-exit",
        install_method="poap-install-no-reload",
        upgrade_path_approved=True,
    )
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps(profiles))
    settings = settings.model_copy(
        update={
            "inventory_provider": "local-yaml",
            "local_inventory_path": path,
            "netbox_url": "",
            "netbox_token": None,
            "execution_mode": mode,
            "allow_unqualified_lab": True,
            "catalog_path": catalog,
            "ssh_public_key_file": Path("tests/fixtures/validation.pub"),
        }
    )
    observed = {**records["observed"], "current_version": entry["software"]["target_version"]}
    provider = build_provider(settings)
    with TestClient(create_app(settings, provider, db)) as client:
        assert client.get("/ready").status_code == 200
        response = client.post("/api/v1/ztp/register", json=observed)
        assert response.status_code == 200, response.text
        result = response.json()
        headers = {"Authorization": "Bearer " + result["status_token"]}
        if mode != "planning-only":
            assert (
                client.get(
                    "/api/v1/ztp/config/" + result["provisioning_id"], headers=headers
                ).status_code
                == 200
            )
            make_due(db)
            validator = Mock()
            validator.validate.return_value = ("valid", {"reason": "verified"})
            assert validate_once(db, provider, validator, settings)
            assert (
                client.get(
                    "/api/v1/ztp/status/" + result["provisioning_id"], headers=headers
                ).json()["state"]
                == "VALIDATED"
            )
        entry["ztp_enabled"] = False
        path.write_text(yaml.safe_dump({"schema_version": 1, "devices": [entry]}))
        assert (
            client.get(
                "/api/v1/ztp/status/" + result["provisioning_id"], headers=headers
            ).status_code
            == 403
        )
        assert yaml.safe_load(path.read_text())["devices"][0]["device"]["status"] == "staged"
