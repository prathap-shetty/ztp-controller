import hashlib
import hmac
import time
from types import SimpleNamespace

from pydantic import SecretStr

from app.dashboard_auth import TTL, authenticated


def test_dashboard_cookie_signature_expiry_and_rotation():
    secret = "operator-token-with-enough-entropy"
    config = SimpleNamespace(dashboard_token=SecretStr(secret))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=config)), cookies={})

    def cookie(expiry):
        message = str(expiry) + ".nonce"
        return (
            message + "." + hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
        )

    request.cookies["ztp_dashboard"] = cookie(int(time.time()) + TTL - 1)
    assert authenticated(request)
    request.cookies["ztp_dashboard"] += "tampered"
    assert not authenticated(request)
    request.cookies["ztp_dashboard"] = cookie(int(time.time()) - 1)
    assert not authenticated(request)
    request.cookies["ztp_dashboard"] = cookie(int(time.time()) + TTL - 1)
    config.dashboard_token = SecretStr("rotated-operator-token-value")
    assert not authenticated(request)
