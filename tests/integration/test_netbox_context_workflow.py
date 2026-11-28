import hashlib

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app

pytestmark = pytest.mark.postgres


def test_lite_netbox_auto_uses_context_not_catalog(settings, netbox, records, db, tmp_path):
    image = tmp_path / "lab-nxos.bin"
    image.write_bytes(b"test-image-content")
    ztp = records["device"]["config_context"]["ztp"]
    ztp["target_nxos"] = "10.5(4)"
    ztp["image"]["sha256"] = hashlib.sha256(image.read_bytes()).hexdigest()
    observed = {**records["observed"], "current_version": "10.4(4)M"}
    settings = settings.model_copy(
        update={
            "lite_mode": True,
            "admin_password": SecretStr("TestPassword123"),
            "catalog_source": "auto",
            "catalog_path": tmp_path / "nonexistent-catalog.json",
            "image_directory": tmp_path,
            "skip_source_validation": True,
            "execution_mode": "upgrade-and-configure",
            "allow_unqualified_lab": True,
        }
    )
    with TestClient(create_app(settings, netbox[0], db)) as client:
        response = client.post("/api/v1/ztp/register", json=observed)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["manifest"]["compatibility_profile"]["id"] == "netbox-1"
        assert body["manifest"]["actions"] == ["download-image", "install-image", "stage-config"]
        assert body["manifest"]["target"]["image_checksum"] == ztp["image"]["sha256"]
        headers = {"Authorization": "Bearer " + body["status_token"]}
        image_response = client.get("/api/v1/ztp/image/" + body["provisioning_id"], headers=headers)
        assert image_response.status_code == 200
        assert image_response.content == image.read_bytes()
        # Existing reauthorization still stops serving when NetBox intent changes.
        ztp["image"]["sha256"] = "b" * 64
        assert (
            client.get("/api/v1/ztp/config/" + body["provisioning_id"], headers=headers).status_code
            == 409
        )


def test_lite_netbox_nxos_without_software_is_configured_only(
    settings, netbox, records, db, tmp_path
):
    ztp = records["device"]["config_context"]["ztp"]
    ztp.pop("target_nxos")
    ztp.pop("image")
    # Any running release: no target is compared and no image is needed.
    observed = {**records["observed"], "current_version": "9.3(8)"}
    settings = settings.model_copy(
        update={
            "lite_mode": True,
            "admin_password": SecretStr("TestPassword123"),
            "catalog_source": "auto",
            "catalog_path": tmp_path / "nonexistent-catalog.json",
            "image_directory": tmp_path,
            "execution_mode": "upgrade-and-configure",
            "allow_unqualified_lab": True,
        }
    )
    with TestClient(create_app(settings, netbox[0], db)) as client:
        response = client.post("/api/v1/ztp/register", json=observed)
        assert response.status_code == 200, response.text
        manifest = response.json()["manifest"]
        assert manifest["mode"] == "configuration-only"
        assert manifest["actions"] == ["stage-config"]
        assert manifest["upgrade_required"] is False
        assert manifest["target"] is None and manifest["compatibility_profile"] is None
        body = response.json()
        headers = {"Authorization": "Bearer " + body["status_token"]}
        assert (
            client.get("/api/v1/ztp/image/" + body["provisioning_id"], headers=headers).status_code
            == 409
        )
        config = client.get("/api/v1/ztp/config/" + body["provisioning_id"], headers=headers)
        assert config.status_code == 200 and "interface mgmt0" in config.text

    # Partial software intent is rejected rather than silently skipping the upgrade.
    ztp["target_nxos"] = "10.5(4)"
    records["device"]["serial"] = records["observed"]["serial_number"]
    with TestClient(create_app(settings, netbox[0], db)) as client:
        partial = client.post("/api/v1/ztp/register", json=observed)
        assert partial.status_code == 403
        assert partial.json()["error"]["code"] == "nxos_software_incomplete"
