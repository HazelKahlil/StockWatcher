# StockWatcher 项目索引

> 状态：2026-08-07 macOS 收口中；本文件只记录已核验事实，不代表商业发布。

## 唯一权威开发目录

- 路径：`~/Documents/700-AI-Workspace/20-Projects/StockWatcher`
- 分支：本地 `main`
- 应用代码基线 / 审计冻结 HEAD：`88ccf49f91fa814af83a004232315286feca3fb7`。
- 收口治理文档在该代码基线之上提交；最终 Git HEAD 以审计报告和 `git rev-parse HEAD` 为准。治理提交不改变 App 二进制，不为追平 HEAD 覆盖安装 App。
- Shared Core 与 Desktop App Mac 以该代码基线及本目录为权威；不从 ZIP、Finder 副本或旧 worktree 开发。

## 三条轨道

| 轨道 | 当前事实 | 状态 | 下一步 |
| --- | --- | --- | --- |
| Shared Core | Provider/Transport、全市场扫描、股票池/缓存、板块、1/3/5 分钟特征、CandidateEngine、StableTop3、StrongMovementDetector、固定时点、历史/总结、Selection Audit、SQLite/迁移均在 main | `accepted`（内部试用基线） | 继续 Mac 真实交易窗口验收，不改业务口径 |
| Desktop App / Mac | `~/Applications/StockWatcher.app`；`SOURCE_COMMIT`=`88ccf49f...`，与应用代码基线一致；治理文档提交不改 App；arm64、ad-hoc 签名验证通过；可执行文件 SHA-256 见 `docs/tracks/app-mac.md` | `internal_trial` | 新鲜 09:45/14:45、15:30 准点、冷启动/睡眠/网络恢复补验 |
| Web 内部测试 | 分支 `web/internal-test-v1`，HEAD=`87a8b856...`，基线=`502a447...`；独立 worktree 在 `90-Archive/StockWatcher/00-current/web/` | `blocked / not_accepted` | 修复 validator 环境门，取得 VPS/域名授权后再做 Linux 与完整交易日验收；不得合入 main |
| Windows | 只保留历史交接、现有 packaging/scripts 与规划文档；未创建未来活跃分支 | `planned` | 真正开始时从已验证 main HEAD 创建 `windows/internal-test-v1`，只做 Windows 平台适配和独立 Live 验收 |

## Tags

最新本地 tags：

- `mac-v1-reliability-rc4-source-20260807`
- `mac-v1-reliability-rc3-20260806`
- `mac-v1-reliability-rc2-20260806`
- `mac-v1-internal-rc1-20260801`
- `v0.0.0`

## 归档入口

- 本轮审计报告：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/90-cleanup-reports/consolidation-20260807-2354/`
- Mac 当前资料：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/app-mac/`
- Web 当前资料：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/web/`
- Windows 历史交接：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/windows/historical-handoffs/`
- 历史资料：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/10-history/`
- 恢复 Bundle、旧工作区和重复候选：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/30-backups/`

## 部署与域名

- Desktop App 只在本机 macOS 内部使用。
- Web 的 Compose/Caddy/Worker 资产只属于独立 Web 线；当前没有已核验的生产域名或 VPS 部署，不得称为已上线。
- Web 线的部署源在其独立 worktree 的 `deploy/`；main 中不复制 Web 选股内核或部署实现。
- 凭据只走 macOS Keychain / Windows Credential Manager / Web 端 Owner 输入的安全边界；索引不保存实际 secret。

## 当前开放问题

1. Mac 新鲜固定时点 Top3、15:30 准点报告、真实冷启动与睡眠/断网恢复仍未全部完成。
2. Windows 真实 M0、交易时段、通知、安装/卸载/回滚仍为 `FAIL` 或未验证；Mac 证据不能替代。
3. Web 当前完整 pytest 现场为 `391 passed, 20 skipped, 2 deselected`，但 workspace validator 被 `.venv` 内 Playwright 示例坏链阻断，VPS/live 验收仍 pending，因此状态仍是 `blocked/not_accepted`。
4. `import/rc4-strict-audit` 的 patch-id 与 main 中 `6078337` 等价但不是 ancestry 上的祖先；按禁止 force 删除规则保留并记录，不重新应用。

## 规则

- 不访问 GitHub，不 fetch/pull/push，不创建 PR/Release。
- 不读取或清理 Keychain、用户数据库、真实运行日志、Application Support 或已安装 App。
- 不复制 CandidateEngine、StableTop3、调度器或 Shared Core；Web 只通过服务/适配器消费共享内核。
