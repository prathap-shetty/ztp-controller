"""Generate the standalone Python 3 IOS XR configuration-only bootstrap."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def release(controller, output, allow_http=False):
    url = urlsplit(controller)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
    ):
        raise ValueError("Controller must be an HTTP(S) origin")
    if url.scheme == "http" and not allow_http:
        raise ValueError("HTTP requires --allow-http")
    script = (
        Path("poap/cisco/iosxr.py")
        .read_text()
        .replace(
            'CONTROLLER_URL = "https://controller.example.invalid"',
            "CONTROLLER_URL = " + json.dumps(controller.rstrip("/")),
        )
        .replace("ALLOW_HTTP = False", "ALLOW_HTTP = " + repr(allow_http))
    )
    compile(script, "iosxr.py", "exec")
    output.mkdir(parents=True, exist_ok=True)
    (output / "iosxr.py").write_text(script)
    (output / "iosxr.py.sha256").write_text(hashlib.sha256(script.encode()).hexdigest() + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-http", action="store_true")
    args = parser.parse_args()
    release(args.controller, args.output, args.allow_http)
