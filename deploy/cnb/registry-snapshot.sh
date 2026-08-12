#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=runtime-lib.sh
source "$script_dir/runtime-lib.sh"

stockwatcher_cnb_prepare_runtime
stockwatcher_cnb_export_app_env

action=${1:-}
RESULT_TAG=cnb-results-latest
SECRET_TAG=cnb-secrets-v1

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

pull_artifact() {
  local reference=$1
  local error_file=$2

  if docker pull "$reference" >/dev/null 2>"$error_file"; then
    return 0
  fi
  if grep -Eqi 'manifest unknown|manifest.*not found|no such manifest|not found' \
    "$error_file"; then
    return 3
  fi
  cat "$error_file" >&2
  return 1
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
    --tag "$image:$RESULT_TAG" \
    --tag "$image:cnb-results-$day_tag" \
    "$temporary_dir" >/dev/null
  docker push "$image:$RESULT_TAG" >/dev/null
  docker push "$image:cnb-results-$day_tag" >/dev/null
  docker image rm \
    "$image:$RESULT_TAG" \
    "$image:cnb-results-$day_tag" >/dev/null 2>&1 || true

  date -Iseconds >"$SW_CNB_BACKUPS/last-upload.txt"
  echo "CNB result snapshot uploaded: $day_tag"
)

restore_snapshot() (
  require_registry

  local temporary_dir image reference container_id pull_error pull_status
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
  reference="$image:$RESULT_TAG"
  pull_error="$temporary_dir/pull-error.txt"
  if pull_artifact "$reference" "$pull_error"; then
    :
  else
    pull_status=$?
    if [[ $pull_status -eq 3 ]]; then
      echo "CNB result snapshot is not initialized" >&2
    fi
    return "$pull_status"
  fi
  container_id=$(docker create "$reference" /bin/true)
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

create_key_snapshot() (
  require_registry

  local temporary_dir image reference pull_error pull_status source_commit
  temporary_dir=$(mktemp -d "$SW_CNB_RUNTIME/tmp/key-snapshot.XXXXXX")
  reference=""
  cleanup_key_snapshot() {
    if [[ -n "$reference" ]]; then
      docker image rm "$reference" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$temporary_dir"
  }
  trap cleanup_key_snapshot EXIT
  export DOCKER_CONFIG="$temporary_dir/docker-config"
  mkdir -p "$DOCKER_CONFIG"
  registry_login

  image=$(artifact_image)
  reference="$image:$SECRET_TAG"
  pull_error="$temporary_dir/pull-error.txt"
  if pull_artifact "$reference" "$pull_error"; then
    echo "CNB key escrow already exists; refusing to overwrite" >&2
    return 2
  else
    pull_status=$?
    if [[ $pull_status -ne 3 ]]; then
      return "$pull_status"
    fi
  fi

  install -m 0400 "$SW_CNB_MASTER_KEY" \
    "$temporary_dir/stockwatcher_master_key"
  (
    cd "$temporary_dir"
    sha256sum stockwatcher_master_key >stockwatcher_master_key.sha256
  )
  printf '%s\n' \
    'FROM scratch' \
    'COPY stockwatcher_master_key /stockwatcher/stockwatcher_master_key' \
    'COPY stockwatcher_master_key.sha256 /stockwatcher/stockwatcher_master_key.sha256' \
    >"$temporary_dir/Dockerfile"

  source_commit=${CNB_COMMIT:-$(git -C "$SW_CNB_WORKSPACE" rev-parse HEAD)}
  docker build \
    --label "org.opencontainers.image.created=$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --label "org.opencontainers.image.revision=$source_commit" \
    --label "org.opencontainers.image.title=StockWatcher CNB key escrow" \
    --tag "$reference" \
    "$temporary_dir" >/dev/null
  docker push "$reference" >/dev/null

  date -Iseconds >"$SW_CNB_BACKUPS/last-key-upload.txt"
  echo "CNB key escrow initialized"
)

restore_key_snapshot() (
  require_registry

  local temporary_dir image reference container_id pull_error pull_status
  temporary_dir=$(mktemp -d "$SW_CNB_RUNTIME/tmp/key-restore.XXXXXX")
  reference=""
  container_id=""
  export DOCKER_CONFIG="$temporary_dir/docker-config"
  mkdir -p "$DOCKER_CONFIG"
  cleanup_key_restore() {
    if [[ -n "$container_id" ]]; then
      docker rm "$container_id" >/dev/null 2>&1 || true
    fi
    if [[ -n "$reference" ]]; then
      docker image rm "$reference" >/dev/null 2>&1 || true
    fi
    rm -rf -- "$temporary_dir"
  }
  trap cleanup_key_restore EXIT
  registry_login

  image=$(artifact_image)
  reference="$image:$SECRET_TAG"
  pull_error="$temporary_dir/pull-error.txt"
  if pull_artifact "$reference" "$pull_error"; then
    :
  else
    pull_status=$?
    if [[ $pull_status -eq 3 ]]; then
      echo "CNB key escrow is not initialized" >&2
    fi
    return "$pull_status"
  fi
  container_id=$(docker create "$reference" /bin/true)
  docker cp "$container_id:/stockwatcher/stockwatcher_master_key" \
    "$temporary_dir/stockwatcher_master_key"
  docker cp "$container_id:/stockwatcher/stockwatcher_master_key.sha256" \
    "$temporary_dir/stockwatcher_master_key.sha256"
  (
    cd "$temporary_dir"
    sha256sum -c stockwatcher_master_key.sha256 >/dev/null
  )
  python - "$temporary_dir/stockwatcher_master_key" <<'PY'
import base64
import sys
from pathlib import Path

encoded = Path(sys.argv[1]).read_bytes().strip()
try:
    decoded = base64.urlsafe_b64decode(encoded)
except Exception as error:
    raise SystemExit("invalid CNB key escrow encoding") from error
if len(decoded) != 32:
    raise SystemExit("invalid CNB key escrow length")
PY
  install -m 0400 "$temporary_dir/stockwatcher_master_key" \
    "$SW_CNB_MASTER_KEY.tmp"
  mv "$SW_CNB_MASTER_KEY.tmp" "$SW_CNB_MASTER_KEY"
  echo "CNB key escrow restored and verified"
)

case "$action" in
  create)
    create_snapshot
    ;;
  restore)
    restore_snapshot
    ;;
  create-key)
    create_key_snapshot
    ;;
  restore-key)
    restore_key_snapshot
    ;;
  *)
    echo "Usage: $0 {create|restore|create-key|restore-key}" >&2
    exit 2
    ;;
esac
