"""Prepare one shared PoC image catalog and HTTP bootstrap (standard library only)."""

import argparse
import hashlib
import json
import re
from pathlib import Path

from release_bootstrap import release


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--target", default="10.5(4)M")
    parser.add_argument("--controller", default="http://10.10.10.1")
    parser.add_argument("--catalog", type=Path, default=Path("catalog/poc.json"))
    parser.add_argument("--bootstrap", type=Path, default=Path("deploy/bootstrap"))
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.bin", args.image.name):
        parser.error("Invalid image filename")
    for value in args.model + args.source + [args.target]:
        if not re.fullmatch(r"[A-Za-z0-9_.():-]{1,128}", value):
            parser.error("Invalid model or version")
    h = hashlib.sha256()
    with args.image.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != args.sha256.lower():
        parser.error("Image checksum mismatch")
    profiles = [
        dict(
            id="poc-" + str(n),
            model=model.upper(),
            source_versions=args.source,
            target_version=args.target,
            image_name=args.image.name,
            image_checksum=h.hexdigest(),
            image_size_bytes=args.image.stat().st_size,
            hardware_qualified=False,
            upgrade_path_approved=True,
            install_method="poap-install-no-reload",
            replay_method="scheduled-config-exit",
        )
        for n, model in enumerate(dict.fromkeys(args.model), 1)
    ]
    args.catalog.parent.mkdir(parents=True, exist_ok=True)
    with args.catalog.open("x") as handle:
        json.dump(profiles, handle, indent=2)
        handle.write("\n")
    release(Path("poap/cisco/poap.py"), args.bootstrap, args.controller, allow_http=True)
    print("PoC catalog and HTTP bootstrap prepared for the specified model/source combinations.")


if __name__ == "__main__":
    main()
