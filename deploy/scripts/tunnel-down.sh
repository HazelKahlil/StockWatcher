#!/usr/bin/env bash
set -Eeuo pipefail

DOCKER=/usr/local/bin/docker

root=${STOCKWATCHER_DEPLOY_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
cd "$root"
env_file=${1:-.env.tunnel}

if [[ ! -x "$DOCKER" ]]; then
  echo "docker not found at $DOCKER" >&2
  exit 2
fi

compose=("$DOCKER" compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file "$env_file")
"${compose[@]}" stop cloudflared tunnel-gateway worker web

if "${compose[@]}" run --rm --no-deps -T web python -s -c '
import sqlite3
import sys
from pathlib import Path

db_path = Path("/var/lib/stockwatcher/db/stockwatcher.db")
if not db_path.is_file():
    print("wal_checkpoint skipped: main database missing")
    raise SystemExit(0)
connection = sqlite3.connect(str(db_path))
try:
    row = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
finally:
    connection.close()
print("wal_checkpoint TRUNCATE:", row)
'; then
  echo "WAL checkpoint completed"
else
  echo "warning: WAL checkpoint failed; shutdown continues" >&2
fi
