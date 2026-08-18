#!/usr/bin/env bash
# Hourly verified SQLite backup for the Mac tunnel stack.
# Rotates only auto-* directories. Never writes tokens or passwords.
set -Eeuo pipefail

DOCKER=/usr/local/bin/docker
PYTHON=/usr/bin/python3
DATE=/bin/date
MKDIR=/bin/mkdir
TEE=/usr/bin/tee
GREP=/usr/bin/grep

root=${STOCKWATCHER_DEPLOY_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}
cd "$root"
env_file=${1:-.env.tunnel}
log_file=${STOCKWATCHER_BACKUP_LOG:-$HOME/Library/Logs/stockwatcher-backup.log}
host_root=${STOCKWATCHER_HOST_BACKUP_DIR:-$HOME/StockWatcherBackups}
keep=${STOCKWATCHER_BACKUP_KEEP:-48}
image=${STOCKWATCHER_BACKUP_IMAGE:-stockwatcher-web:web-alpha4-34ce825}
backup_volume=${STOCKWATCHER_BACKUP_VOLUME:-stockwatcher_stockwatcher_tunnel_backups}

"$MKDIR" -p "$(dirname "$log_file")" "$host_root"

log() {
  local ts
  ts=$("$DATE" '+%Y-%m-%dT%H:%M:%S%z')
  echo "$ts $*" | "$TEE" -a "$log_file" >/dev/null
  echo "$ts $*"
}

if [[ ! -x "$DOCKER" ]]; then
  log "docker not found at $DOCKER"
  exit 2
fi

if [[ ! -f "$env_file" ]]; then
  log "missing $root/$env_file"
  exit 2
fi

if ! "$PYTHON" - "$DOCKER" <<'PY'
import subprocess
import sys

docker = sys.argv[1]
try:
    result = subprocess.run(
        [docker, "info"],
        timeout=8,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
except subprocess.TimeoutExpired:
    sys.exit(1)
except Exception:
    sys.exit(1)
sys.exit(0 if result.returncode == 0 else 1)
PY
then
  log "docker info failed or timed out; skip backup"
  exit 1
fi

stamp=$("$DATE" -u +%Y%m%dT%H%M%SZ)
name="auto-${stamp}"
output="/backups/${name}"
compose=("$DOCKER" compose -f docker-compose.yml -f docker-compose.tunnel.yml --env-file "$env_file")

log "starting backup $name"

web_running=0
if "${compose[@]}" ps --status running --format '{{.Service}}' 2>/dev/null | "$GREP" -qx web; then
  web_running=1
fi

if [[ "$web_running" -eq 1 ]]; then
  "${compose[@]}" exec -T web \
    python -m stock_watcher.server.admin_cli backup --output "$output"
else
  "${compose[@]}" run --rm --no-deps -T web \
    python -m stock_watcher.server.admin_cli backup --output "$output"
fi

verify_and_copy=$("$DOCKER" run --rm --user 0 \
  -v "${backup_volume}:/backups" \
  -v "${host_root}:/export" \
  "$image" \
  python -s -c '
import hashlib
import shutil
import sys
from pathlib import Path

name = sys.argv[1]
keep = int(sys.argv[2])
src_root = Path("/backups") / name
if not src_root.is_dir():
    raise SystemExit(f"backup directory missing: {src_root}")
inners = sorted(
    path for path in src_root.iterdir()
    if path.is_dir() and path.name.startswith("stockwatcher-")
)
if not inners:
    raise SystemExit(f"no stockwatcher-* snapshot under {src_root}")
snapshot = inners[-1]
sums = snapshot / "SHA256SUMS.txt"
if not sums.is_file():
    raise SystemExit(f"SHA256SUMS.txt missing in {snapshot}")
failed = 0
for line in sums.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    digest, rel = line.split(None, 1)
    path = snapshot / rel
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        print(f"CHECKSUM_FAIL {rel}", file=sys.stderr)
        failed += 1
    else:
        print(f"CHECKSUM_OK {rel}")
if failed:
    raise SystemExit(f"{failed} checksum failures in {snapshot}")
print(f"SHA256SUMS_PASS {snapshot}")

dst = Path("/export") / name
if dst.exists():
    raise SystemExit(f"host destination already exists: {dst}")
shutil.copytree(src_root, dst)
print(f"HOST_COPY_OK {dst}")


def rotate(parent: Path) -> None:
    dirs = sorted(
        path for path in parent.iterdir()
        if path.is_dir() and path.name.startswith("auto-")
    )
    extra = dirs[:-keep] if keep >= 0 else dirs
    for path in extra:
        shutil.rmtree(path)
        print(f"ROTATED {path}")


rotate(Path("/backups"))
rotate(Path("/export"))
' "$name" "$keep")

echo "$verify_and_copy"
echo "$verify_and_copy" | while IFS= read -r line; do
  log "$line"
done

log "backup complete $name"
