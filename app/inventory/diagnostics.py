"""Name the inventory field that failed validation without exposing its value."""

from pydantic import ValidationError


def invalid_field(exc) -> str:
    """Return a dotted field name for a validation/lookup error, or "unknown"."""
    if isinstance(exc, ValidationError):
        errors = exc.errors()
        if errors and errors[0].get("loc"):
            return ".".join(str(part) for part in errors[0]["loc"])
    if isinstance(exc, KeyError) and exc.args:
        return str(exc.args[0])
    return "unknown"
