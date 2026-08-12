# CNB Web 运行说明

此配置只运行独立 Web 线，不运行 macOS/Windows 桌面端，也不扩展为任意网页抓取。
数据入口仍是项目已经批准的 Tushare SDK/API；生产 Token 只通过 HTTPS 管理页录入。

## 时间与资源

- CNB 系统时区为 `Asia/Shanghai`。
- 工作日 `08:25` 启动 2 核 only-preview 工作空间，预留构建和启动时间。
- Web 与唯一 Worker 运行到 `16:15`，覆盖目标 `09:00–16:00`；随后完成最终快照并退出。
- only-preview 离线保活为 8 小时，低于平台 18 小时上限；没有伪造心跳或规避回收。
- 平台故障、额度耗尽和供应商异常仍可能中断，因此这是运行目标与监测边界，不是 100% SLA。

## 状态与制品

- `.cnb-runtime/` 由 CNB 工作空间 `backup: true` 备份，包含 SQLite、报告及只读主密钥文件；
  该目录被 Git 忽略。
- `11:35`、`15:55`、`16:10` 和正常退出时用 SQLite Online Backup API 生成一致性快照。
- 快照以仓库私有 Docker 制品标签 `cnb-results-latest` 与 `cnb-results-YYYYMMDD` 保存；制品只含数据库、报告、校验和，
  不含主密钥。首次进入全新工作空间时会校验 SHA-256 后恢复。
- 若工作空间备份与主密钥同时丢失，结果制品仍可恢复，但加密 Token 必须由 Owner 重新录入。
  不把主密钥与结果制品放在同一恢复包中。

## 一次性设置

1. 在 CNB 专用仓库的 `main` 分支点“首次设置 Web”。该分支映射本地独立 Web 配置线，
   不是 StockWatcher 本地日常开发 `main`。
2. 在打开的私有终端执行 `bash deploy/cnb/bootstrap-admin.sh`，在终端中输入管理员密码；密码不写
   命令行、Git、日志或制品。脚本会拒绝首尾空格或制表符，避免不可见字符造成登录失败。
3. 关闭运维空间，点“立即启动 Web”。登录后在 HTTPS 管理页录入 Tushare Token。
4. 次日从 `09:00` 开始按 Web 交易日验收清单观察。未完成现场验收前，状态保持
   `BLOCKED / NOT_ACCEPTED`。

若管理员已经创建但密码无法登录，在私有终端执行
`bash deploy/cnb/bootstrap-admin.sh reset`。该模式只重置已有账号的密码并撤销其旧会话；密码仍只从
标准输入读取，不会出现在命令参数或输出中。

手动按钮运行两小时，便于非交易时段检查。自动任务只绑定独立分支，不影响本地 `main`、现有
Mac Docker、域名或 Cloudflare Tunnel。
