import shutil

import pytest

from app.inventory.netbox import identity
from app.models.contracts import DeviceIdentity
from app.services.hashing import stable_hash
from app.services.templates import render_template, role_template

IDENTITY = {
    "id": "1",
    "name": "leaf-1",
    "serial_number": "ABC123",
    "vendor": "cisco",
    "platform": "nxos",
    "model": "N9K-C9300V",
    "status": "staged",
}


def test_identity_without_role_serializes_and_hashes_as_before():
    device = DeviceIdentity(**IDENTITY)
    assert "role" not in device.model_dump(mode="json")
    assert stable_hash(device.model_dump(mode="json")) == stable_hash(IDENTITY)
    leaf = DeviceIdentity(**IDENTITY, role="leaf")
    assert leaf.model_dump(mode="json")["role"] == "leaf"
    assert DeviceIdentity.model_validate(leaf.model_dump(mode="json")) == leaf


@pytest.mark.parametrize("key", ["role", "device_role"])
def test_netbox_identity_reads_role_slug(records, key):
    record = records["device"]
    record.pop("role", None)
    record.pop("device_role", None)
    record[key] = {"id": 3, "name": "Leaf", "slug": "leaf"}
    assert identity(record, record["platform"]["slug"]).role == "leaf"
    record.pop(key)
    assert identity(record, record["platform"]["slug"]).role is None


@pytest.fixture
def vendor(tmp_path):
    shutil.copy("templates/cisco/iosxe_lite.j2", tmp_path / "iosxe_lite.j2")
    roles = tmp_path / "roles" / "iosxe"
    roles.mkdir(parents=True)
    (roles / "leaf.j2").write_text("ip domain lookup\nbanner motd ^{{ hostname }}^\n")
    (tmp_path / "secret.j2").write_text("must not be included\n")
    return tmp_path / "iosxe_lite.j2"


class Address:
    ip, netmask = "10.10.10.5", "255.255.255.0"

    def __str__(self):
        return "10.10.10.5/24"


VALUES = {
    "hostname": "edge-1",
    "password": "TestPassword123",
    "management": {
        "address": Address(),
        "gateway": "10.10.10.1",
        "interface": "GigabitEthernet1",
    },
}


def test_role_include_is_rendered_before_end_and_changes_revision(vendor):
    common, common_revision = render_template(vendor, "iosxe", None, **VALUES)
    leaf, leaf_revision = render_template(vendor, "iosxe", "leaf", **VALUES)
    assert leaf.endswith("ip domain lookup\nbanner motd ^edge-1^\nend")
    assert common.endswith("10.10.10.1\nend") and "ip domain lookup" not in common
    assert leaf_revision != common_revision
    # A role without a file gets only the common configuration.
    assert render_template(vendor, "iosxe", "border", **VALUES) == (common, common_revision)


@pytest.mark.parametrize("role", ["../secret", "Leaf", "leaf/../../secret", "", None])
def test_role_slug_never_selects_a_path_outside_its_folder(vendor, role):
    assert role_template(vendor, "iosxe", role) is None
