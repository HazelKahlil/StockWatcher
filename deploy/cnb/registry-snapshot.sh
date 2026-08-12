#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=runtime-lib.sh
source "$script_dir/runtime-lib.sh"

stockwatcher_cnb_prepare_runtime
stockwatcher_cnb_export_app_env

action=${1:-}

require_registry() {
  : "${CNB_DOCKER_REGISTRY:?CNB Docker registry is unavailable}"
  : "${CNB_REPO_SLUG_LOWERCASE:?CNB repository slug is unavailable}"
  : "${CNB_TOKEN_USER_NAME:?CNB registry username is unavailable}"
  : "${CNB_TOKEN:?CNB temporary token is unavailable}"
}

registry_login() {
  printf '%s' "$CNB_TOKEN" | docker login \
    "$CNB_DOCKER_REGISTRY" \
    --username "$CNB_TOKEN_USER_NAME" \
    --password-stdin >/dev/null
}

artifact_image() {
  printf '%s/%s' \
    "$CNB_DOCKER_REGISTRY" \
    "$CNB_REPO_SLUG_LOWERCASE"
}

create_snapshot() (
  require_registry

  local temporary_dir backup_root backup_dir image day_tag source_commit container_file
  temporary_dir=$(mktemp -d "$SW_CNB_RUNTIME/tmp/snapshot.XXXXXX")
  trap 'rm -rf -- "$temporary_dir"' EXIT
  export DOCKER_CONFIG="$temporary_dir/docker-config"
  mkdir -p "$DOCKER_CONFIG"
  registry_login
  backup_root="$temporary_dir/backup-root"
  mkdir -p "$backup_root"

  python -m stock_watcher.server.admin_cli backup --output "$backup_root" \
    >"$temporary_dir/backup-result.json"
  backup_dir=$(find "$backup_root" -mindepth 1 -maxdepth 1 -type d -print | sort | tail -n 1)
  [[ -n "$backup_dir" ]]

  tar -C "$backup_dir" -czf "$temporary_dir/backup.tar.gz" .
  (
    cd "$temporary_dir"
    sha256sum backup.tar.gz >backup.tar.gz.sha256
  )

  container_file="$temporary_dir/Dockerfile"
  printf '%s\n' \
    'FROM scratch' \
    'COPY backup.tar.gz /stockwatcher/backup.tar.gz' \
    'COPY backup.tar.gz.sha256 /stockwatcher/backup.tar.gz.sha256' \
    >"$container_file"

  image=$(artifact_image)
  day_tag=$(date +%Y%m%d)
  source_commit=${CNB_COMMIT:-$(git -C "$SW_CNB_WORKSPACE" rev-parse HEAD)}
  docker build \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --label "org.opencontainers.image.revision=$source_commit" \
    --label "org.opencontainers.image.title=StockWatcher CNB result snapshot" \
    --tag "$image:cnb-results-latest" \
    --tag "$image:cnb-results-$day_tag" \
    "$temporary_dir" >/dev/null
  docker push "$image:cnb-results-latest" >/dev/null
  docker push "$image:cnb-results-$day_tag" >/dev/null
  docker image rm \
    "$image:cnb-results-latest" \
    "$image:cnb-results-$day_tag" >/dev/null 2>&1 || true

  date -Iseconds >"$SW_CNB_BACKUPS/last-upload.txt"
  echo "CNB result snapshot uploaded: $day_tag"
)

restore_snapshot() (
  require_registry

  local temporary_dir image container_id
  temporary_dir=$(mktemp -d "$SW_CNB_RUNTIME/tmp/restore.XXXXXX")
  container_id=""
  export DOCKER_CONFIG="$temporary_dir/docker-config"
  mkdir -p "$DOCKER_CONFIG"
  cleanup_restore() {
    if [[ -n "$container_id" ]]; then
      docker rm "$container_id" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$temporary_dir"
  }
  trap cleanup_restore EXIT
  registry_login

  image=$(artifact_image)
  docker pull "$image:cnb-results-latest" >/dev/null
  container_id=$(docker create "$image:cnb-results-latest" /bin/true)
  docker cp "$container_id:/stockwatcher/backup.tar.gz" \
    "$temporary_dir/backup.tar.gz"
  docker cp "$container_id:/stockwatcher/backup.tar.gz.sha256" \
    "$temporary_dir/backup.tar.gz.sha256"
  (
    cd "$temporary_dir"
    sha256sum -c backup.tar.gz.sha256 >/dev/null
  )

  mkdir -p "$temporary_dir/verified"
  python - "$temporary_dir/backup.tar.gz" "$temporary_dir/verified" <<'PY'
import sys
import tarfile
from pathlib import Path

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
with tarfile.open(archive, mode="r:gz") as handle:
    handle.extractall(destination, filter="data")
PY
  python -m stock_watcher.server.admin_cli restore \
    --input "$temporary_dir/verified" >/dev/null
  echo "CNB result snapshot restored and verified"
)

case "$action" in
  create)
    create_snapshot
    ;;
  restore)
    restore_snapshot
    ;;
  *)
    echo "Usage: $0 {create|restore}" >&2
    exit 2
    ;;
esac
