#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=runtime-lib.sh
source "$script_dir/runtime-lib.sh"

stockwatcher_cnb_prepare_runtime
stockwatcher_cnb_export_app_env

key_restored=0
result_restored=0
if "$script_dir/registry-snapshot.sh" restore-key; then
  key_restored=1
else
  restore_status=$?
  if [[ $restore_status -ne 3 ]]; then
    echo "无法核验 CNB 私有密钥制品；已停止管理员设置" >&2
    exit "$restore_status"
  fi
fi

if [[ ! -s "$SW_CNB_DB" ]]; then
  if "$script_dir/registry-snapshot.sh" restore; then
    result_restored=1
  else
    restore_status=$?
    if [[ $restore_status -ne 3 ]]; then
      echo "无法核验 CNB 结果制品；已停止管理员设置" >&2
      exit "$restore_status"
    fi
  fi
fi

if [[ $result_restored -eq 1 && $key_restored -ne 1 ]]; then
  echo "发现结果制品但缺少对应私有密钥；为避免损坏 Token，已停止设置" >&2
  exit 1
fi

python -m stock_watcher.server.admin_cli migrate >/dev/null

mode=${1:-create}
if [[ "$mode" != "create" && "$mode" != "reset" ]]; then
  echo "用法: $0 [create|reset]" >&2
  exit 2
fi

read -r -p "管理员用户名 [admin]: " username
username=${username:-admin}
read -r -s -p "管理员密码（至少 12 位）: " password
printf '\n'
read -r -s -p "再次输入管理员密码: " password_confirm
printf '\n'

if [[ "$password" != "$password_confirm" ]]; then
  unset password password_confirm
  echo "两次密码不一致；未创建账号" >&2
  exit 1
fi

if [[ "$password" =~ ^[[:space:]] || "$password" =~ [[:space:]]$ ]]; then
  unset password password_confirm
  echo "密码首尾不能包含空格或制表符；未保存密码" >&2
  exit 1
fi

if [[ "$mode" == "reset" ]]; then
  printf '%s\n' "$password" | \
    python -m stock_watcher.server.admin_cli reset-password \
      --username "$username" \
      --password-stdin
else
  printf '%s\n' "$password" | \
    python -m stock_watcher.server.admin_cli create-user \
      --username "$username" \
      --role admin \
      --password-stdin
fi
unset password password_confirm

if [[ $key_restored -ne 1 ]]; then
  "$script_dir/registry-snapshot.sh" create-key
fi
"$script_dir/registry-snapshot.sh" create

date -Iseconds >"$SW_CNB_RUNTIME/admin-bootstrapped.txt"
echo "管理员凭据与首次恢复快照已保存。请关闭运维空间，再点“立即启动 Web”，并在 HTTPS 管理页录入 Token。"
