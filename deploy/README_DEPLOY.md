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

## macOS internal test through Cloudflare Tunnel

This route keeps FastAPI, the single Worker and SQLite on the owner Mac while exposing only an
outbound Cloudflare Tunnel. It is for the 2–5 person internal test lane; the Mac and Docker Desktop
must remain awake and online. It does not replace VPS or full trading-day acceptance evidence.

1. Create a named, locally managed tunnel and route `stock.hazelkahlil.com` to it. Keep the generated
   credential JSON outside Git.
2. Copy `.env.tunnel.example` to `.env.tunnel`, set the final image/commit metadata, tunnel UUID,
   credential path and local UID/GID. Never put the Tushare Token or any password in this file.
3. Generate `secrets/stockwatcher_master_key` locally without displaying its contents, then run
   `scripts/tunnel-up.sh`. The production Web and Worker use Docker named volumes; the local gateway
   is bound only to `127.0.0.1`, and `cloudflared` makes outbound connections to Cloudflare.
4. Run `scripts/tunnel-healthcheck.sh`, create the first admin through stdin, then enter the Tushare
   Token only through the HTTPS Admin page. Stop containers with `scripts/tunnel-down.sh`; named
   volumes are deliberately preserved.
