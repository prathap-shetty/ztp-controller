"""Generate an unapproved upgrade profile from an operator-supplied Cisco image."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("image", type=Path)
    p.add_argument(
        "--approved-sha256", required=True, help="Digest independently obtained from Cisco"
    )
    p.add_argument("--model", default="N9K-C93180YC-FX3")
    p.add_argument("--source", default="10.4(4)M")
    p.add_argument("--target", default="10.5(4)M")
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()
    h = hashlib.sha256()
    with args.image.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != args.approved_sha256.lower():
        p.error("Image does not match independently approved SHA-256")
    profile = [
        {
            "id": "c93180-fx3-upgrade-lab",
            "model": args.model,
            "source_versions": [args.source],
            "target_version": args.target,
            "image_name": args.image.name,
            "image_checksum": h.hexdigest(),
            "image_size_bytes": args.image.stat().st_size,
            "hardware_qualified": False,
            "upgrade_path_approved": False,
            "install_method": "poap-install-no-reload",
            "replay_method": "scheduled-config-exit",
        }
    ]
    # Never overwrite a previously approved catalog accidentally.
    with args.output.open("x") as handle:
        json.dump(profile, handle, indent=2)
        handle.write("\n")
    print("Unapproved profile created. Review upgrade path and exact CLI releases before enabling.")


if __name__ == "__main__":
    main()
