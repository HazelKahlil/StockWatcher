"""Offline contract tests for CNB private registry persistence."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_SCRIPT = PROJECT_ROOT / "deploy" / "cnb" / "registry-snapshot.sh"
PREVIEW_SCRIPT = PROJECT_ROOT / "deploy" / "cnb" / "run-preview.sh"


def _write_fake_docker(target: Path) -> None:
    target.write_text(
        """#!/usr/bin/env bash
set -Eeuo pipefail

command_name=${1:?missing docker command}
shift
local_dir=${FAKE_DOCKER_LOCAL:?}
store_dir=${FAKE_DOCKER_STORE:?}

case "$command_name" in
  login)
    cat >/dev/null
    ;;
  pull)
    if [[ ${FAKE_DOCKER_PULL_ERROR:-} == "auth" ]]; then
      echo "unauthorized" >&2
      exit 1
    fi
    if [[ ! -s "$store_dir/stockwatcher_master_key" ]]; then
      echo "manifest unknown: not found" >&2
      exit 1
    fi
    rm -rf -- "$local_dir"
    mkdir -p "$local_dir"
    cp "$store_dir"/stockwatcher_master_key* "$local_dir/"
    ;;
  build)
    context=${!#}
    rm -rf -- "$local_dir"
    mkdir -p "$local_dir"
    cp "$context"/stockwatcher_master_key* "$local_dir/"
    ;;
  push)
    rm -rf -- "$store_dir"
    mkdir -p "$store_dir"
    cp "$local_dir"/stockwatcher_master_key* "$store_dir/"
    ;;
  image)
    [[ ${1:-} == "rm" ]]
    rm -rf -- "$local_dir"
    ;;
  create)
    echo "fake-container"
    ;;
  cp)
    source_path=${1:?}
    destination=${2:?}
    cp "$local_dir/${source_path##*/}" "$destination"
    ;;
  rm)
    ;;
  *)
    echo "unsupported fake docker command: $command_name" >&2
    exit 64
    ;;
esac
""",
        encoding="utf-8",
    )
    target.chmod(0o700)


def _run_registry(action: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(REGISTRY_SCRIPT), action],
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_cnb_key_escrow_is_private_non_overwriting_and_recoverable(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_docker(fake_bin / "docker")
    runtime_dir = tmp_path / "runtime"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CNB_BUILD_WORKSPACE": str(PROJECT_ROOT),
        "CNB_COMMIT": "offline-test-commit",
        "CNB_DOCKER_REGISTRY": "docker.invalid",
        "CNB_REPO_SLUG_LOWERCASE": "private/stockwatcher-web",
        "CNB_TOKEN_USER_NAME": "offline-test-user",
        "CNB_TOKEN": "offline-test-token",
        "STOCKWATCHER_CNB_RUNTIME_DIR": str(runtime_dir),
        "FAKE_DOCKER_LOCAL": str(tmp_path / "local-image"),
        "FAKE_DOCKER_STORE": str(tmp_path / "private-registry"),
    }

    missing = _run_registry("restore-key", env)
    assert missing.returncode == 3
    assert "not initialized" in missing.stderr

    created = _run_registry("create-key", env)
    assert created.returncode == 0, created.stderr
    key_path = runtime_dir / "secrets" / "stockwatcher_master_key"
    original_key = key_path.read_bytes()
    assert original_key.decode("ascii").strip() not in created.stdout + created.stderr

    shutil.rmtree(runtime_dir)
    restored = _run_registry("restore-key", env)
    assert restored.returncode == 0, restored.stderr
    assert key_path.read_bytes() == original_key
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o400
    assert original_key.decode("ascii").strip() not in restored.stdout + restored.stderr

    duplicate = _run_registry("create-key", env)
    assert duplicate.returncode == 2
    assert "refusing to overwrite" in duplicate.stderr
    assert key_path.read_bytes() == original_key

    auth_failure = _run_registry(
        "create-key",
        {**env, "FAKE_DOCKER_PULL_ERROR": "auth"},
    )
    assert auth_failure.returncode == 1
    assert "unauthorized" in auth_failure.stderr
    assert key_path.read_bytes() == original_key


def test_cnb_preview_fails_closed_and_snapshots_while_running() -> None:
    script = PREVIEW_SCRIPT.read_text(encoding="utf-8")
    key_restore = script.index('registry-snapshot.sh" restore-key')
    result_restore = script.index('registry-snapshot.sh" restore;')
    web_start = script.index("python -m stock_watcher.server.web")

    assert key_restore < result_restore < web_start
    assert "starting with a new private database" not in script
    assert 'backup_interval_seconds=${STOCKWATCHER_CNB_BACKUP_INTERVAL_SECONDS:-900}' in script
    assert 'create_snapshot "$(date +%Y%m%d)-periodic-$periodic_slot"' in script
    assert 'create_snapshot "$(date +%Y%m%d)-shutdown"' in script

    pipeline = (PROJECT_ROOT / ".cnb.yml").read_text(encoding="utf-8")
    assert 'STOCKWATCHER_CNB_BACKUP_INTERVAL_SECONDS: "900"' in pipeline
    assert 'STOCKWATCHER_CNB_BACKUP_INTERVAL_SECONDS: "60"' in pipeline
