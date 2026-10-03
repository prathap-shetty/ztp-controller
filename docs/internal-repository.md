# Import ZTP Lite into an internal repository

This branch provides a standalone working tree. Its Git history still contains
older lab and alternate-deployment files. To avoid importing that history, export
a fresh snapshot, inspect it and initialize a new repository:

```bash
mkdir -p /tmp/ztp-lite-internal
git archive codex/ztp-lite-only | tar -x -C /tmp/ztp-lite-internal
cd /tmp/ztp-lite-internal
git init -b main
git add .
git commit -m 'Import ZTP Lite'
git remote add origin YOUR_INTERNAL_REPOSITORY_URL
git push -u origin main
```

Use a new empty directory for the export. Do not copy the original `.git`, local
`.env*`, inventory, generated catalog/bootstrap or firmware directories. The
archive contains tracked source/examples only. Confirm your organization's source
import and dependency policies before publication; this branch is not a legal
license grant or a security certification. Preserve third-party notices, including
`poap/cisco/UPSTREAM.md` where present. No internal remote is configured for you.

## Compatibility with existing Lite deployments

The branch keeps `compose.lite.yaml` and project name `ztp-lite`, so existing named
volumes are reused if you keep the same project name. Back up PostgreSQL first.
Changes to local environment values needed when adopting this branch:

- Rename `POC_ADMIN_PASSWORD` to `ZTP_ADMIN_PASSWORD`, preserving its value.
- Use `deploy/lite/kea.json` and `deploy/lite/nginx.conf` instead of `deploy/poc/`.
- Existing catalog paths can remain unchanged; new setups default to `catalog/lite.json`.
- Use `scripts/prepare_lite.py` instead of `prepare_poc.py` for new catalogs.

Do not use `down -v` during an update. Existing runtime policy fields retain legacy
names internally so this packaging change does not alter established behavior.
