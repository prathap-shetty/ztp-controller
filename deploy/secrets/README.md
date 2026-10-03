# Local M1 credentials

Create `secrets/netbox_token` outside version control containing a NetBox token
with read-only permissions for devices, config context and IP addresses. The
Compose services mount it as `/run/secrets/netbox_token`. Set the correct Token
or Bearer authorization scheme for your deployed NetBox token type.

For the development-only stack, `.env` holds a unique URL-safe PostgreSQL
password (URL-safe characters avoid connection-URL escaping). Both files must
be readable only by the operator and the relevant container process. Compose
bind-mounted secrets must be readable by the application UID 10001 on Linux;
configure ownership/ACLs for that UID rather than making secrets world-readable.
Do not commit credentials. `docker compose config` can expand the database
password: use `docker compose config --quiet` for validation.

This M1 stack binds the API to localhost and does not serve physical switches.
M2a adds separate public-key and worker-only private-key directories plus nginx
TLS mounts; see docs/m2a-lab-guide.md. Database credentials remain a development
.env setting pending production secret management. The API supports `ZTP_NETBOX_TOKEN_FILE`; a direct
`ZTP_NETBOX_TOKEN` is available for development, never both together.
