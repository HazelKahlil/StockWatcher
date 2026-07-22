# StockWatcher

StockWatcher 是供 2—3 名内部用户使用的 A 股候选观察与异动提醒工具。它从全市场行情中筛出三只值得进一步看盘的候选，并提供固定时点和盘中特别强异动提醒。

> 本项目只提供候选观察和异动提醒，不构成投资建议；不得读取交易密码、连接交易账户或自动下单。

## 当前状态

- 项目治理与 V2.0 交接基线已建立。
- 当前开发电脑是 Mac，采用“本地优先、GitHub 版本节点同步”；产品代码尚未开始。
- 下一目标是 `v0.1-mac-replay-foundation`：先完成跨平台工程骨架、Mock/Replay 和确定性测试。
- Windows/通达信真实数据 M0 延后到 `v0.3-windows-data-gate`。在该版本通过前，不得声称紫黄线、Windows 通知或安装包已经验证。
- GitHub 私有仓库保留为里程碑镜像、远端备份和交接入口，不承担日常迭代。

## 从这里开始

1. 阅读 [AGENTS.md](AGENTS.md)。
2. 按 [docs/README.md](docs/README.md) 的文档地图恢复项目状态。
3. 阅读 [锁定业务项](docs/reference/v2.0/requirements.lock.json) 和 [V2.0 规格](docs/reference/v2.0/SPEC_V2.0_AGENT.md)。
4. 开始任何实现前，读取 [v0.1 Mac Replay 基础版](docs/visions/v0.1-mac-replay-foundation/README.md)，创建执行 issue，再把版本从“计划中”更新为“进行中”。

## 项目基线

| 项 | 当前约定 |
| --- | --- |
| 当前开发环境 | Mac 本地，Asia/Shanghai |
| 原规格目标环境 | Windows 桌面端 + 通达信；当前无可用 Windows 电脑，留到独立环境门验证 |
| 计划技术栈 | Python 3.11/3.12、PySide6、SQLite WAL、YAML + Pydantic、pytest |
| v0.1 数据口径 | Mock / Replay / Synthetic；不接真实交易账户，不把模拟数据冒充实时行情 |
| 完整版主数据口径 | 通达信最新正式版或后续确认的合法兼容数据源；准确字段与授权以真实 M0 为准 |
| 默认提醒 | 09:45、14:50；盘中特别强异动最多 3 批/日 |
| 输出 | 数据健康时每批固定三只，标记“强 / 中 / 近” |
| 安全边界 | 不读取账户，不自动交易，不用旧数据伪装正常结果 |

## 本地开发方式

- 日常工作全部在本地 Git 仓库完成：短分支 → 本地验证 → 本地提交 → 合并回本地 `main`。
- 每个 session 收尾先保证本地 `main` 可恢复；不自动 push，不为每个小改动创建 GitHub PR。
- 版本节点、显式备份/交接需求或用户明确要求时，再从本地 `main` 创建 `publish/<version>` 分支，统一 push，并用一个 PR 同步 GitHub。
- `origin/main` 只代表最近一次已发布里程碑；本地优先模式下，日常事实以本地 `main` 为准。

## Bootstrap 验证

```bash
python3 scripts/validate_workspace.py
git diff --check
```

产品实现开始后，测试、lint 和打包命令必须在 `AGENTS.md` 与目标版本 README 中补齐，未配置的命令不得被宣称为已验证。

## 版本路线

项目先在 Mac 完成可回放基础与本地 Alpha，再进入真实 Windows/通达信数据闸门，之后接入完整 V1 并稳定化。详见 [版本索引](docs/visions/README.md)；本地与 GitHub 同步规则见 [release.md](docs/process/release.md)。
