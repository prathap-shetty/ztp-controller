import io
from urllib.error import HTTPError

from test_m2a import load_bootstrap


def test_bootstrap_uses_allowlisted_explanation_only():
    module = load_bootstrap()
    error = HTTPError(
        "http://controller",
        403,
        "Forbidden",
        {},
        io.BytesIO(b'{"error":{"code":"platform_missing","message":"SECRET PASSWORD"}}'),
    )
    message = str(module.controller_rejection(error))
    assert "platform_missing" in message
    assert "SECRET" not in message
    error = HTTPError("http://controller", 500, "Failure", {}, io.BytesIO(b"private traceback"))
    assert str(module.controller_rejection(error)) == "ZTP controller rejected request: HTTP 500"
