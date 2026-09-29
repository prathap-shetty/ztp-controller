"""Open only the catalog-selected regular image, without following symlinks."""

import os
import re
import stat
from pathlib import Path

from app.errors import ZtpError


def open_image(directory: Path, profile: dict):
    name = profile["image_name"]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.bin", name):
        raise ZtpError("invalid_image", 409, "Invalid image name")
    descriptor = None
    try:
        descriptor = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size != profile["image_size_bytes"]:
            raise ValueError("Invalid image")
        return os.fdopen(descriptor, "rb")
    except (OSError, ValueError):
        if descriptor is not None:
            os.close(descriptor)
        raise ZtpError(
            "image_unavailable", 409, "Approved image missing or size mismatch"
        ) from None
