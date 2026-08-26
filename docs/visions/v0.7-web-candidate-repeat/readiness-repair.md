# Web `/health/ready` 503 返修

> 状态: 已部署到 Mac 内测隧道；readiness P1 已关闭；Web 继续 `BLOCKED / NOT_ACCEPTED`
> 任务锚点: 独立运行健康返修，repeat 功能冻结
> 创建: 2026-08-26

## 节点

| 项 | 值 |
| --- | --- |
| 起始 HEAD | `bf07c2284eba38c30dd5009e3d2aeb673dd71a3c` |
| 修复提交 | `db10869def7242ecfd66975eb0cb97d0c4203d29` |
| 分支 | `fix/web-readiness-503` |
| 新镜像 | `stockwatcher-web:web-repeat-ready-db10869` |
| 镜像 digest | `sha256:1fdcf4f420d8097fb3e14ad4ab2053e7c2c3850c5964bbd7cfe97717889803d5` |
| SOURCE_COMMIT | `db10869def7242ecfd66975eb0cb97d0c4203d29` |
| BUILD_VERSION | `web-repeat-ready-db10869` |
| 回退镜像 | `stockwatcher-web:web-repeat-4b1e79e` / `sha256:cbee6514797747197ba3744e03a3e46ddce6455956a9bd169191b55ad3a34c39` |
| 灾难回退 | `stockwatcher-web:web-alpha4-34ce825` + v9 备份 |
| 修复前一致性备份 | `/backups/ready-fix-20260826T054917Z/stockwatcher-20260826T134917Z` SHA-256 `e52335855ba22bfb5ecc78af42e85706924393dfd6e4434a704ed683eadec2b8` |
| 部署前一致性备份 | `/backups/ready-deploy-20260826T061233Z/stockwatcher-20260826T141234Z` SHA-256 `2cc879a6c9a87703ba4cb6fcbb22f2f3a918a4c3f050473a04b376d89cca42c1` |
| GitHub | 未 push |

## 复现

隔离项目 `sw-ready-fix` 使用当时 live 镜像 `web-repeat-4b1e79e` 与 v10 备份副本、独立端口 `127.0.0.1:18081`、无生产 Tunnel。Worker 持有 lease 且主循环推进时，同一时刻 CLI / `worker_readiness()` / `_readiness_status()` / 容器内 HTTP / 宿主机 HTTP / Docker health 全部 ready。

审计时现网 Web 从 12:10 起 unhealthy（failing_streak 后升到 100+），Worker 12:45 才 healthy。HTTP `/health/ready` 为 503 `{"status":"not_ready"}`，同一容器新进程里 `worker_readiness()` 为 True。处理函数把任意异常压成最小 503，服务器无分阶段日志，因此当时拿不到 exception_type。

## 修复

公开失败正文仍为 `{"status":"not_ready"}`。

服务器记录 `event=web_readiness_failed`、exception_type、failure_stage、source_commit、expected_schema_version、request_id，堆栈脱敏（路径替换为 `<db_path>`，Token/Cookie 走 `redact()`）。

HTTP 探测每次新建 read-only `SQLiteStore`，读完关闭连接，并 `PRAGMA busy_timeout=5000`。Schema / lease / runtime heartbeat / worker.loop / stalled scan 门槛不变。未改 Docker healthcheck 命令，未改 Worker CLI 的 `integrity_check`。

## 验证

- pytest：575 passed, 25 skipped, 2 deselected；health 文件 16 passed
- ruff / mypy / validate_workspace / check_windows_package / `git diff --check` / `uv lock --check`：通过
- JS 未修改
- engine / providers / repeat_tracker / sqlite.py diff vs `4b1e79e`：空
- 隔离 100 次 `/health/ready` 间隔 ≥1s：全部 200
- 隔离 Web/Worker Docker health：healthy
- 隔离整栈 `compose down/up`：双方 healthy，HTTP 200
- 现网只替换 Web；Worker 仍为 `web-repeat-4b1e79e`
- 现网 Schema 仍为 v10；未执行 migration；未执行 repeat backfill
- 隧道 origin 50 次 `/health/ready`：全部 200
- 公网（浏览器 UA）：`/health/live` 200、`/health/ready` 200 `{"status":"ready"}`、登录页 200
