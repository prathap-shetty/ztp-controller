"""Lite configuration templates with optional per-role includes."""

import hashlib
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

# NetBox role slugs; anything else never selects a file.
ROLE_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def role_template(template: Path, platform: str, role: str | None) -> str | None:
    """Relative include path of templates/<vendor>/roles/<platform>/<role>.j2, if present."""
    if not role or not ROLE_SLUG.fullmatch(role):
        return None
    relative = "roles/" + platform + "/" + role + ".j2"
    return relative if (template.parent / relative).is_file() else None


def render_template(template: Path, platform: str, role: str | None, **values) -> tuple[str, str]:
    """Render a template; return the configuration and its template revision.

    The common template includes the role file through ``role_template``. Without a
    role file the revision is the common template hash, as before role support.
    """
    include = role_template(template, platform, role)
    environment = Environment(
        loader=FileSystemLoader(str(template.parent)),
        undefined=StrictUndefined,
        autoescape=False,
        # The include line then leaves no blank line when a device has no role file.
        trim_blocks=True,
    )
    rendered = environment.get_template(template.name).render(
        role=role or "", role_template=include, **values
    )
    common = template.read_text().encode()
    if include is None:
        return rendered, hashlib.sha256(common).hexdigest()
    role_text = (template.parent / include).read_bytes()
    revision = hashlib.sha256(common + b"\0" + include.encode() + b"\0" + role_text)
    return rendered, revision.hexdigest()
