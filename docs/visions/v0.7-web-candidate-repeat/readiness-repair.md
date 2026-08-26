# Web `/health/ready` 503 返修

> 状态: 代码与测试已落在 `fix/web-readiness-503`；隔离稳定性与受控部署见下文完成项
> 任务锚点: 独立运行健康返修，repeat 功能冻结
> 创建: 2026-08-26

## 范围

只处理 Web HTTP `/health/ready` 与 Docker health。评分、排名、Stable Top3、强异动、repeat 表与 v10 迁移不在本任务。

## 复现记录

隔离项目 `sw-ready-fix`，镜像 `stockwatcher-web:web-repeat-4b1e79e`，数据库为 live v10 只读一致性备份副本：

- 备份: `/backups/ready-fix-20260826T054917Z/stockwatcher-20260826T134917Z`
- SQLite SHA-256: `e52335855ba22bfb5ecc78af42e85706924393dfd6e4434a704ed683eadec2b8`
- Schema: v10，`integrity_check=ok`，`foreign_key_check=0`

同一时刻探针（Worker 已持有 lease、主循环在推进）全部为 ready/200/healthy。审计时现网 Web 从 12:10 起 unhealthy，Worker 在 12:45 才 healthy；HTTP 503 与新进程里 `worker_readiness()=True` 并存，因为处理函数把异常压成 `{"status":"not_ready"}` 且无服务器日志。

## 修复

- 公开正文失败时仍为 `{"status":"not_ready"}`
- `event=web_readiness_failed` 分阶段脱敏日志（不含 Token/Cookie/路径/库内容）
- HTTP 探测每次打开新的 read-only `SQLiteStore`，读完关闭连接，并设置 `busy_timeout=5000`
- Schema / lease / runtime heartbeat / worker.loop / stalled scan 门槛不变
