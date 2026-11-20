import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import SecretStr
from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliConnectionError

from app.services.lite_verification import (
    LiteSshValidator,
    fingerprint,
    keys_conflict,
    lite_configuration_check,
)

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
OTHER = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBzZ2yJ8l8wB0bKqGq3m0rG2PX0b8Yb0bZ5e9C2m3l0q"

XE_CONFIG = """! IOS XE
hostname lab-c8kv-01
username admin privilege 15 secret 0 TestPassword123
crypto key generate rsa general-keys label ZTP-LITE-SSH modulus 2048
ip ssh version 2
interface GigabitEthernet1
 ip address 10.10.10.130 255.255.255.0
 no shutdown
ip route 0.0.0.0 0.0.0.0 10.10.10.1
end
"""
XE_RUNNING = """Building configuration...
hostname lab-c8kv-01
username admin privilege 15 secret 9 $9$hashed
ip ssh version 2
interface GigabitEthernet1
 ip address 10.10.10.130 255.255.255.0
ip route 0.0.0.0 0.0.0.0 10.10.10.1
end
"""
EOS_CONFIG = """! Arista EOS
hostname lab-veos-01
username admin privilege 15 role network-admin secret 0 TestPassword123
!
interface Management1
   ip address 10.10.10.120/24
!
ip route 0.0.0.0/0 10.10.10.1
!
management ssh
   no shutdown
!
end
"""
EOS_RUNNING = """hostname lab-veos-01
username admin privilege 15 role network-admin secret sha512 $6$hash
interface Management1
   ip address 10.10.10.120/24
ip route 0.0.0.0/0 10.10.10.1
end
"""
CISCO_INVENTORY = 'NAME: "Chassis", DESCR: "Cisco"\nPID: {pid}, VID: V00, SN: {serial}\n'


def settings():
    return SimpleNamespace(admin_password=SecretStr("TestPassword123"))


def connection_with(outputs):
    class Responses(list):
        failed = False

    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.send_commands.return_value = Responses(
        SimpleNamespace(result=output) for output in outputs
    )
    return Mock(return_value=connection)


def claim(platform, model, serial, body, address, manifest=None, evidence=None):
    return {
        "intent": {"management": {"address": address}},
        "observed": {"platform": platform, "model": model, "serial_number": serial},
        "manifest": manifest or {},
        "config_body": body,
        "evidence": evidence,
    }


def test_configuration_check_ignores_secrets_and_platform_defaults():
    assert lite_configuration_check(XE_CONFIG, XE_RUNNING) == (True, [])
    assert lite_configuration_check(EOS_CONFIG, EOS_RUNNING) == (True, [])
    ok, missing = lite_configuration_check(EOS_CONFIG, EOS_RUNNING.replace("10.10.10.1\n", "\n"))
    assert not ok and missing == ["ip route 0.0.0.0/0 10.10.10.1"]
    # An explicit opposite of an omitted default fails.
    shut = XE_RUNNING.replace(" ip address 10.10.10.130 255.255.255.0\n", " shutdown\n")
    assert not lite_configuration_check(XE_CONFIG, shut)[0]
    assert not any("TestPassword123" in line for line in lite_configuration_check(XE_CONFIG, "")[1])


def test_host_key_pin_detects_a_different_key_of_the_same_type():
    assert not keys_conflict([KEY], [KEY])
    assert keys_conflict([KEY], [OTHER])
    assert keys_conflict([KEY], ["ecdsa-sha2-nistp256 AAAA"])
    assert fingerprint(KEY).startswith("SHA256:")


@pytest.mark.parametrize(
    ("platform", "driver", "model", "serial", "body", "outputs", "commands"),
    [
        (
            "iosxe",
            "cisco_iosxe",
            "C8000V",
            "9ABCDEF1234",
            XE_CONFIG,
            [
                "Cisco IOS XE Software, Version 17.12.4",
                CISCO_INVENTORY.format(pid="C8000V", serial="9ABCDEF1234"),
                XE_RUNNING,
                XE_RUNNING,
            ],
            4,
        ),
        (
            "eos",
            "arista_eos",
            "VEOS-LAB",
            "0CCCDE59095C",
            EOS_CONFIG,
            [
                json.dumps(
                    {
                        "modelName": "vEOS-lab",
                        "serialNumber": "",
                        "systemMacAddress": "0c:cc:de:59:09:5c",
                        "version": "4.30.7M",
                    }
                ),
                EOS_RUNNING,
                EOS_RUNNING,
            ],
            3,
        ),
        (
            "iosxr",
            "cisco_iosxr",
            "R-IOSXRV9000-CC",
            "XR123",
            "hostname lab-xr-01\nssh server v2\n",
            [
                "Cisco IOS XR Software, Version 24.4.1",
                CISCO_INVENTORY.format(pid="R-IOSXRV9000-CC", serial="XR123"),
                "hostname lab-xr-01\nssh server v2\n",
            ],
            3,  # Committed XR configuration has no separate startup-config.
        ),
    ],
)
def test_lite_verification_per_platform(platform, driver, model, serial, body, outputs, commands):
    factory = connection_with(outputs)
    validator = LiteSshValidator(settings(), factory, ping=lambda h: True, keyscan=lambda h: [KEY])
    result, evidence = validator.validate(claim(platform, model, serial, body, "10.10.10.5/24"))
    assert (result, evidence["reason"]) == ("valid", "verified")
    assert all(evidence["checks"].values())
    assert evidence["host_keys"] == [KEY] and evidence["host_key"] == fingerprint(KEY)
    kwargs = factory.call_args.kwargs
    assert kwargs["platform"] == driver and kwargs["host"] == "10.10.10.5"
    assert kwargs["auth_strict_key"] is True and kwargs["auth_username"] == "admin"
    assert kwargs["auth_password"] == "TestPassword123"
    sent = factory.return_value.send_commands.call_args.args[0]
    assert len(sent) == commands and all(c.startswith("show ") for c in sent)


def test_nxos_checks_target_version_and_identity():
    nxos = (
        "hostname leaf-2\nfeature ssh\ninterface mgmt0\n  ip address 10.10.10.9/24\n  no shutdown\n"
    )
    running = "hostname leaf-2\ninterface mgmt0\n  ip address 10.10.10.9/24\n"
    inventory = {
        "TABLE_inv": {
            "ROW_inv": [
                {"name": '"Chassis"', "productid": "N9K-C9500V", "serialnum": "NXOSV0SERIAL"}
            ]
        }
    }
    outputs = [json.dumps({"nxos_ver_str": "10.4(3)"}), json.dumps(inventory), running, running]
    manifest = {"target": {"target_version": "10.4(3)"}, "allow_newer_version": True}
    check = claim("nxos", "N9K-C9500V", "NXOSV0SERIAL", nxos, "10.10.10.9/24", manifest)
    validator = LiteSshValidator(
        settings(), connection_with(outputs), ping=lambda h: True, keyscan=lambda h: [KEY]
    )
    assert validator.validate(check)[0] == "valid"
    check["observed"]["serial_number"] = "OTHER"
    result, evidence = validator.validate(check)
    assert (result, evidence["reason"]) == ("fatal", "device_identity_mismatch")


def test_failures_are_reported_before_and_after_login():
    body, address = "hostname x\n", "10.10.10.5/24"
    no_ping = LiteSshValidator(settings(), Mock(), ping=lambda h: False, keyscan=lambda h: [KEY])
    assert (
        no_ping.validate(claim("eos", "VEOS-LAB", "S", body, address))[1]["reason"] == "ping_failed"
    )
    changed = LiteSshValidator(settings(), Mock(), ping=lambda h: True, keyscan=lambda h: [OTHER])
    result, evidence = changed.validate(
        claim("eos", "VEOS-LAB", "S", body, address, evidence={"host_keys": [KEY]})
    )
    assert (result, evidence["reason"]) == ("fatal", "host_key_changed")
    assert evidence["host_keys"] == [KEY]
    # The system transport raises a connection error after repeated password prompts.
    repeated = Mock(
        side_effect=ScrapliConnectionError(
            "password prompt seen more than once, assuming auth failed"
        )
    )
    prompts = LiteSshValidator(settings(), repeated, ping=lambda h: True, keyscan=lambda h: [KEY])
    assert prompts.validate(claim("eos", "VEOS-LAB", "S", body, address))[1]["reason"] == (
        "ssh_login_failed"
    )
    refused = Mock(side_effect=ScrapliAuthenticationFailed("denied"))
    login = LiteSshValidator(settings(), refused, ping=lambda h: True, keyscan=lambda h: [KEY])
    assert login.validate(claim("eos", "VEOS-LAB", "S", body, address))[1]["reason"] == (
        "ssh_login_failed"
    )
