"""Generate the standalone Python 3 IOS XE configuration-only Guest Shell bootstrap."""

import argparse
from pathlib import Path

from release_xr_bootstrap import release

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-http", action="store_true")
    args = parser.parse_args()
    release(args.controller, args.output, args.allow_http, Path("poap/cisco/iosxe.py"))
