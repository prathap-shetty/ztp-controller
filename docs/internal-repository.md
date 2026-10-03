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
Use `.env.lite.example` as the settings reference when adopting this branch:

- Set `ZTP_ADMIN_PASSWORD` to the intended switch admin password.
- DHCP and HTTP configuration live in `deploy/lite/`.
- Existing catalog paths can remain unchanged; new setups default to `catalog/lite.json`.
- Use `scripts/prepare_lite.py` for new catalogs.
- The Python mode setting is `lite_mode` (`ZTP_LITE_MODE` in the environment).
  The Compose file enables it automatically. Older mode/password setting names
  are no longer supported; recreate containers with the updated Compose file.

Do not use `down -v` during an update. This naming update does not change provisioning policy.
