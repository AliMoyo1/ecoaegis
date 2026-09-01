# EcoAegis production deployment

This deployment keeps the shared Caddy gateway separate from the EcoAegis
application stack. Only Caddy publishes host ports. EcoAegis joins the shared
`edge` network for HTTP traffic and its private `ecoaegis_backend` network for
PostgreSQL. PostgreSQL has no published port.

## Release identity

The initial production release is based on commit
`3240203e27230173cbc223cc8e9d0d15ca13b0d5`. The release directory and the
container label must record that full commit. Do not copy local `.env`, SQLite
databases, ignored attachment data, virtual environments, or test artifacts.

## Required host layout

```text
/srv/edge/
  Caddyfile
  compose.yaml
  sites/
/srv/ecoaegis/
  config/ecoaegis.env
  incoming/
  secrets/app_secret
  secrets/db_password
  releases/<release>/
  current -> releases/<release>
/var/backups/ecoaegis/
```

Secrets are generated on the VPS as hexadecimal text and remain outside the
release directory. The non-secret runtime configuration starts from
`deploy/ecoaegis/ecoaegis.env.example`.

## Pre-deployment gates

1. Verify the repository is clean and `HEAD` equals the intended remote commit.
2. Run the local test suite with the project virtual environment.
3. Inventory host services, listeners, firewall, users, Docker state, storage,
   backups, and application directories.
4. Take a provider snapshot or create a protected configuration rollback
   archive before changing packages, SSH, firewall, or services.
5. Create a non-root deployment account, copy the approved public key, and
   verify a new key-only login. Give it a private `incoming` directory for
   release uploads, but do not add it to the Docker group or grant blanket
   sudo.

## Host access order

Keep the current root key authorized throughout deployment. The `deployer`
account may upload candidate releases into `/srv/ecoaegis/incoming`, but a
verified root session must validate hashes and promote them into the root-owned
release tree. After the new `deployer` login and the retained root key have both
been tested in separate connections:

1. Disable SSH password authentication.
2. Change root SSH policy to `prohibit-password`, which retains the emergency
   root key path.
3. Validate with `sshd -t`, reload SSH, and retest both deployment and root key
   logins before ending existing sessions.
4. Enable UFW only after allow rules for TCP 22, 80, and 443 exist.

## Build and database-backed test

The test profile uses a separate PostgreSQL container with a tmpfs data
directory. It cannot see the production database volume or production network.

```bash
docker network inspect edge >/dev/null
docker compose --profile test -f deploy/ecoaegis/compose.yaml build test
docker compose --profile test -f deploy/ecoaegis/compose.yaml run --rm -T test
docker compose --profile test -f deploy/ecoaegis/compose.yaml down
```

Do not run `python -m sheplatform.seeds.seed` in production. The seed command
contains example Econet records and shared demonstration passwords.

## Start order

```bash
docker compose -f deploy/ecoaegis/compose.yaml config --quiet
docker compose -f deploy/ecoaegis/compose.yaml up -d --build db app
docker compose -f /srv/edge/compose.yaml config --quiet
docker compose -f /srv/edge/compose.yaml up -d
```

Caddy obtains the certificate directly while Cloudflare remains DNS-only. Add
no Cloudflare proxying until public HTTPS, redirects, login assets, the app and
database health check, and certificate renewal storage have all been verified.

## Backups

Run and verify an initial backup before enabling the timer:

```bash
systemctl start ecoaegis-backup.service
systemctl status ecoaegis-backup.service --no-pager
systemctl enable --now ecoaegis-backup.timer
```

Each backup contains a PostgreSQL custom-format dump, attachment archive,
evidence archive, and SHA-256 manifest. Files remain in the private
`/var/backups/ecoaegis` directory for 14 days. Add an encrypted off-host copy
before real organizational data is introduced.

## Release rollback

Application rollback does not restore or overwrite data automatically.

1. Preserve a fresh database and file backup.
2. Point `/srv/ecoaegis/current` at the prior immutable release.
3. Build the prior image tag and run its disposable PostgreSQL test profile.
4. Recreate only the `app` service.
5. Reload Caddy and verify health, logs, and the authenticated route.

If a schema change is not backward compatible, stop and restore the matching
database dump into a disposable PostgreSQL 16 instance first. Validate the dump
before any production restore. The pre-deployment host rollback archive is for
reversing package, SSH, firewall, and service setup; restore its files only from
the provider console or another verified root session.
