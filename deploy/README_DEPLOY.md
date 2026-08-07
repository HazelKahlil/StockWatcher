# Deployment runbook

These files are a reference target for the implementing Agent. Before production use, the Agent must implement every referenced Python entry point, regenerate `uv.lock`, pin image digests, and pass the acceptance plan. The reference host scripts assume a dedicated VPS and are run with `sudo`; the application containers themselves remain non-root UID/GID 10001.

## Host preparation

```bash
cp .env.example .env
# Edit public metadata only, then protect it and create host mounts.
sudo ../scripts/generate-master-key.sh secrets/stockwatcher_master_key
sudo ../scripts/prepare-host.sh
```

Point the domain A/AAAA records to the VPS. Open only SSH, TCP 80/443 and UDP 443 when HTTP/3 is desired.

## Validate and build

```bash
sudo docker compose --env-file .env config >/dev/null
sudo docker compose --env-file .env build --pull
```

The final delivery must replace floating base tags with immutable digests and record the resulting application image digest.

## Migrate and create the first admin

```bash
sudo docker compose --env-file .env run --rm --no-deps web \
  python -m stock_watcher.server.admin_cli migrate

sudo ../scripts/bootstrap-admin.sh admin
```

Do not pass a password on a command line. The wrapper reads it without echo and sends it over stdin.

## Start

```bash
sudo docker compose --env-file .env up -d web worker caddy
sudo ../scripts/healthcheck.sh
```

After HTTPS and login are verified, the Owner enters the Tushare Token in the Admin page. The Token must never be added to `.env`.

## Server data-source preflight

Use the implemented Admin diagnostics/CLI to validate static/ordinary Pro, realtime quote, and 1/100/300/800/full-market scales from the VPS IP. Save machine-readable evidence before a trading-day acceptance run.

## Common operations

```bash
sudo ../scripts/backup.sh
sudo ../scripts/update.sh
sudo ../scripts/restore.sh backups/<backup-dir> --yes

sudo docker compose --env-file .env logs --since 30m web worker caddy
sudo docker compose --env-file .env ps
```

Never delete the database or caches as a first response to failure. Preserve evidence and follow the rollback runbook.
