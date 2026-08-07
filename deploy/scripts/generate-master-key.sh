#!/usr/bin/env bash
set -Eeuo pipefail

[[ ${EUID:-$(id -u)} -eq 0 ]] || {
  echo "Run with sudo/root so the key can be owned by container UID 10001." >&2
  exit 1
}

output=${1:-deploy/secrets/stockwatcher_master_key}
if [[ -e "$output" ]]; then
  echo "Refusing to overwrite existing key: $output" >&2
  exit 1
fi
umask 077
mkdir -p "$(dirname "$output")"
python - "$output" <<'PY'
from pathlib import Path
import base64, os, sys
path=Path(sys.argv[1])
path.write_text(base64.urlsafe_b64encode(os.urandom(32)).decode('ascii')+'\n', encoding='ascii')
PY
chown 10001:10001 "$output"
chmod 400 "$output"
echo "Created master key file at $output for container UID 10001 (content not displayed)."
