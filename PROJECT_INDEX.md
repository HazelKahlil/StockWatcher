# StockWatcher 项目索引

> 状态：2026-08-12 三轨内部试用基准；不是商业发布或完整三平台验收。

> 当前后继开发：`fix/candidate-outcomes-trade-cal-wire-endpoint`（`0.6.0a3` / SQLite v8）
> 已完成 `/trade_cal` 生产 wire endpoint 的本地离线工程验证；收口后合入本地 `main`，
> 不代表真实同点行情验收、GitHub 同步或已安装 App 重建。

## 唯一权威开发目录

- 路径：`~/Documents/700-AI-Workspace/20-Projects/StockWatcher`。
- 分支：本地 `main`。
- 当前应用代码基线：`ad04e392158c7050f84e0318fe1d53aaa0370c34`（Python `0.4.0a2`）。
- 基准版本入口：annotated tag `v0.4.0-alpha.2`；tag 不存在时表示里程碑同步尚未闭环。
- Shared Core 与 Desktop App 源码只从本目录/tag 重建，不从 ZIP、Finder 副本、Web worktree
  或旧 Windows portable 反向恢复。

## 三条交付轨道

| 轨道 | 当前事实 | 状态 | 下一步 |
| --- | --- | --- | --- |
| Shared Core | `ad04e39` 包含 Tushare 主路线、确定性候选、StableTop3、强异动、固定时点、历史/总结、SQLite 安全恢复及 alpha.2 版本元数据 | `internal_trial_source_baseline` | 后续修改从 tag 继续，保持数据健康 fail-closed |
| Desktop App / Mac | 现有 `~/Applications/StockWatcher.app` 的已记录 `SOURCE_COMMIT=88ccf49f...`；本轮未覆盖或重装，早于 alpha.2 源码 | `internal_trial` | 需要新包时从 tag 重建；继续真实固定时点/恢复补验 |
| Web 内部测试 | 独立 `web/internal-test-v1@bf447ba`；Mac Docker + Cloudflare Tunnel 当前可达；完整工程门通过 | **`BLOCKED / NOT_ACCEPTED`** | 补完整交易日、通知/重放、断线与备份恢复；不合 main |
| Windows | PR #4 merge `a5da270` 已进入 main；Windows 3.11/3.12 CI、Setup、PyInstaller/Inno 和制品上传通过 | `WINDOWS_SMOKE_PASS`，非权威 M0 | 需要新包时从 tag fresh clone 重建；现场验收另行进行 |

详细证据与边界见
[v0.4.0-alpha.2 内部基准](docs/visions/v0.4.0-alpha.2-internal-baseline/README.md)。

## Tags

- `v0.4.0-alpha.2`：当前 Mac / Web / Windows 内部试用源码基准；不是 stable release。
- `mac-v1-reliability-rc4-source-20260807`
- `mac-v1-reliability-rc3-20260806`
- `mac-v1-reliability-rc2-20260806`
- `mac-v1-internal-rc1-20260801`
- `v0.0.0`

## 归档入口

- Mac 当前资料：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/app-mac/`。
- Web 当前资料与独立 worktree：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/web/`。
- Windows 历史交接：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/00-current/windows/historical-handoffs/`。
- 历史资料与恢复包：`~/Documents/700-AI-Workspace/90-Archive/StockWatcher/10-history/`、
  `30-backups/`。

## 部署与凭据边界

- Desktop App 只在本机 macOS 内部使用；当前安装资产没有冒充 alpha.2 重建包。
- Web 当前由本机 Mac Docker + Cloudflare Tunnel 提供，仍依赖 Mac 开机、联网和 Docker
  Desktop；VPS 后置，不把当前状态写成独立托管或生产稳定。
- Windows 当前结论是 smoke，不是连续 M0、完整交易日或签名安装器验收。
- 凭据只走 macOS Keychain / Windows Credential Manager / Web Owner 安全流程；索引、Git、
  SQLite、日志、截图和打包制品不保存实际 secret。

## 当前开放问题

1. 已安装 Mac App 尚未从 `0.4.0a2` 重建；新鲜固定时点、15:30 准点和真实睡眠/断网恢复
   仍是现场补验项。
2. Web 继续 `BLOCKED / NOT_ACCEPTED`；浏览器完全关闭后的 Web Push 未实现，完整交易日与
   运行恢复门未完成。
3. Windows 目标机现有 portable 早于最终 SQLite 恢复与 alpha.2 打包元数据；权威 M0、
   安装/卸载/回滚和签名包未验证。
4. GitHub Actions 的 Node.js 20 action 弃用提示为非阻塞 P2，后续独立升级 action 主版本。
5. 次日同点复盘真实 09:45/14:45 现场结算与错过时点回补仍待 Human Owner 在交易日验收；
   当前状态仅为 `local_code_complete_offline_verified_after_wire_endpoint_fix`。

## 规则

- 日常权威仍是本地 `main`；GitHub 只在里程碑节点同步。
- 不读取或清理 Keychain、Credential Manager、用户数据库、真实运行日志、Application
  Support 或已安装 App。
- 不复制 CandidateEngine、StableTop3、调度器或 Shared Core；Web 保持独立 provenance。
- 测试、截图、CI 或公网可访问均不能单独升级为 `ACCEPTED`、正式 M0 或商业稳定结论。
