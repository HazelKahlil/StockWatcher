#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=runtime-lib.sh
source "$script_dir/runtime-lib.sh"

stockwatcher_cnb_prepare_runtime
stockwatcher_cnb_export_app_env
python -m stock_watcher.server.admin_cli migrate >/dev/null

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

printf '%s\n' "$password" | \
  python -m stock_watcher.server.admin_cli create-user \
    --username "$username" \
    --role admin \
    --password-stdin
unset password password_confirm

date -Iseconds >"$SW_CNB_RUNTIME/admin-bootstrapped.txt"
echo "管理员已创建。请关闭运维空间，再点“立即启动 Web”，并在 HTTPS 管理页录入 Token。"
