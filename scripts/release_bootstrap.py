"""Produce a non-secret bootstrap with embedded Cisco MD5 compatibility checksum."""

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def release(source: Path, output: Path, controller: str, allow_http=False, ca_pem=""):
    url = urlsplit(controller)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username
        or url.password
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
    ):
        raise ValueError("Controller must be an HTTP(S) origin")
    if url.scheme != "https" and not allow_http:
        raise ValueError("HTTP requires explicit --allow-http")
    script = source.read_text().replace(
        'CONTROLLER_URL = "https://controller.example.invalid"',
        "CONTROLLER_URL = " + json.dumps(controller.rstrip("/")),
    )
    script = script.replace("ALLOW_HTTP = False", "ALLOW_HTTP = " + repr(allow_http))
    script = script.replace('CA_PEM = ""', "CA_PEM = " + repr(ca_pem))
    script = script.replace("# md5sum=", "#md5sum=")
    without = "".join(
        line for line in script.splitlines(keepends=True) if not line.startswith("#md5sum=")
    )
    md5 = hashlib.md5(without.encode(), usedforsecurity=False).hexdigest()
    script = script.replace('#md5sum="GENERATED_BY_RELEASE_TOOL"', '#md5sum="' + md5 + '"')
    compile(script, "poap.py", "exec")
    output.mkdir(parents=True, exist_ok=True)
    (output / "poap.py").write_text(script)
    (output / "poap.py.sha256").write_text(hashlib.sha256(script.encode()).hexdigest() + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-http", action="store_true")
    parser.add_argument("--ca", type=Path)
    args = parser.parse_args()
    release(
        Path("poap/cisco/poap.py"),
        args.output,
        args.controller,
        args.allow_http,
        args.ca.read_text() if args.ca else "",
    )
