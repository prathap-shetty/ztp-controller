# Repository knowledge

## Scope and branch layout

This repository currently packages **ZTP Lite**, a Linux Docker Compose controller
for Cisco NX-OS POAP, image installation and configuration staging. It is not yet a
multivendor implementation; see `docs/multivendor-implementation-plan.md`.

- `main` contains the Lite-only deployment, MIT license and dashboard reset support.
- `codex/ztp-lite-only` preserves the separate Lite branch.
- `codex/full-controller` was restored with Mac/en7 and alternate deployments.
  Check current refs before relying on this branch description.
- Do not merge Lite-only file removals into a full-controller branch, or restore
  alternate deployments onto main, without an explicit request. Compare trees
  before synchronizing branches; preserve branch-specific settings and templates.
- Do not leave temporary worktrees holding main after finishing work. Remove only
  worktrees you created, after checking for uncommitted and ignored files.

## Main entry points

- `compose.lite.yaml`, `.env.lite.example`: Linux deployment and example settings.
- `deploy/docker/Dockerfile`: application image; preserve non-root readability of
  source files and `alembic.ini` when changing COPY instructions.
- `app/main.py`: FastAPI setup, registration and provisioning endpoints.
- `app/settings.py`: validated settings using the `ZTP_` environment prefix.
- `app/inventory/`: provider interface, factory, NetBox, InfraHub and local YAML.
- `app/models/contracts.py`: normalized device identity and provisioning intent.
- `app/vendors/cisco_nxos.py`, `nxos_version.py`: catalog and release policy.
- `app/services/rendering.py`, `templates/cisco/`: configuration rendering.
- `app/persistence/`, `migrations/`: durable attempts, grants, events and failures.
- `poap/cisco/poap.py`: switch bootstrap; `scripts/release_bootstrap.py` generates
  the served script with its checksum. Regenerate it after bootstrap changes.
- `app/dashboard.py`, `dashboard.html`, `dashboard_auth.py`: dashboard and login.
- `app/reprovision.py`: shared controller reset operation and operator CLI.

## Behavior to preserve

- Inventory sources are `netbox`, `infrahub` and `local-yaml`. Runtime inventory
  providers read inventory; they do not edit source-of-truth records or schemas.
- `ZTP_NETBOX_BRANCH` defaults to `main` when omitted or blank. Non-main values are
  NetBox's eight-character schema IDs, not display names. Send `X-NetBox-Branch`
  on all branch inventory requests; main omits the header and needs no plugin.
- `ZTP_INFRAHUB_BRANCH` also defaults to `main` when omitted or blank. It uses a
  branch name in `/graphql/<branch>` without a trailing slash.
- Attempts retain saved intent. Updating inventory/templates does not silently
  replace an existing attempt; deliberately erased devices can be reprovisioned.
- Reset is available beside View in each device row, with simple confirmation and
  no typed serial. It archives history, revokes old tokens and releases identity
  for a new attempt. It never erases or reboots the switch. Keep authentication,
  action-header checks and stale-attempt protection on the reset endpoint.
- Dashboard login uses a static environment token and an authenticated cookie.
  Do not expose raw tokens, passwords, configurations or inventory error bodies.
- Lite uses HTTP/password login and manual post-boot verification. Configuration
  staged does not mean the device rebooted successfully or was independently
  verified. Do not label it verified without evidence.
- Equal/newer supported numeric NX-OS releases can skip installation under policy;
  `10.5(4)` and `10.5(4)M` are equivalent. Unknown suffixes must not be guessed.
- Source validation bypass does not qualify an upgrade path. Preserve image
  checksum/size checks and original image filenames. Existing conflicting image
  files must not be silently overwritten.
- Record specific controller failure reasons without leaking secrets. A controller
  rejection and a switch-side install/replay failure are different failure stages.

## Development and validation

Python 3.12; dependencies are locked with uv. CI is in `.github/workflows/test.yml`.

```bash
uv sync --frozen
uv run ruff check app migrations tests scripts poap
uv run ruff format --check app migrations tests scripts poap
uv run pytest -q tests/unit
```

Full integration tests require `TEST_DATABASE_URL` pointing to a **disposable**
PostgreSQL database. Fixtures apply migrations and clear tables. Never point tests
at a running lab or production database. With that variable configured:

```bash
uv run pytest -q
POSTGRES_PASSWORD=validation-only ZTP_ADMIN_PASSWORD=TestPassword123 \
  docker compose -f compose.lite.yaml --profile dhcp config --quiet
```

Run checks appropriate to the changed behavior. For dashboard JavaScript, check
syntax and exercise changed actions where possible. Do not claim browser or
hardware validation based solely on unit tests or Compose configuration checks.

## Documentation and operations

Start with `README.md` and `docs/ztp-lite-user-guide.md`. Provider details are in
`docs/local-yaml-inventory.md` and `docs/infrahub-inventory.md`. Update examples and
Compose environment forwarding alongside new settings.

Keep firmware, real inventory, `.env` files, API tokens and device credentials out
of commits. Use placeholder data in examples. Preserve ignored local files when
switching branches. MIT covers this project's code; third-party dependencies and
vendor firmware retain their own licenses.

A previous dev-server rebuild was explicitly put on hold. Do not resume remote
lab deployment merely because code changes are complete; wait for the user's
instruction to resume. Never embed lab credentials in this file.
