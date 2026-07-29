# StockWatcher

StockWatcher 是供 2—3 名内部用户使用的 A 股候选观察与异动提醒工具。它从全市场行情中筛出三只值得进一步看盘的候选，并提供固定时点和盘中特别强异动提醒。

> 本项目只提供候选观察和异动提醒，不构成投资建议；不得读取交易密码、连接交易账户或自动下单。

## 当前状态

- 项目治理与 V2.0 交接基线已建立。
- 当前开发电脑是 Mac，采用“本地优先、GitHub 版本节点同步”；v0.1/v0.2 的 Mac Mock/Replay 范围已本地完成。
- 当前活跃目标是 `v0.3-windows-data-gate`：Windows + 官方 TdxQuant 单人只读测试。前置代码已通过 Mac 回归和独立真实 Windows 的无终端工程/打包验证；Human Owner 的真实 TdxQuant M0 仍未执行。Mac 不购买或接入 Tushare/iFinD。
- Windows 真机 M0 前不得声称紫黄线、真实交易时段、Windows 通知或安装体验已经验证。
- GitHub 私有仓库保留为里程碑镜像、远端备份和交接入口，不承担日常迭代。

## 从这里开始

1. 阅读 [AGENTS.md](AGENTS.md)。
2. 按 [docs/README.md](docs/README.md) 的文档地图恢复项目状态。
3. 阅读 [锁定业务项](docs/reference/v2.0/requirements.lock.json) 和 [V2.0 规格](docs/reference/v2.0/SPEC_V2.0_AGENT.md)。
4. 开始真实数据工作前，读取 [v0.3 Windows 数据闸门](docs/visions/v0.3-windows-data-gate/README.md) 和规则路由表。

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

v0.1 的可复现本地验证（仅 Mac + Mock/Replay）如下：

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run mypy src tests
python3 scripts/validate_workspace.py
git diff --check
```

项目支持 Python 3.11/3.12；`uv.lock` 锁定当前开发环境的依赖解析。不得将上述结果表述为 Windows、通达信、紫黄线或真实行情验证。
直接与开发依赖的用途、许可证与安全影响见 [依赖审计](docs/process/dependencies.md)；变更后必须额外执行 `uv sync --all-groups --frozen` 与 `uv lock --check`。

## 启动 Mac Replay UI Alpha

```bash
uv sync --all-groups --frozen
uv run python -m stock_watcher.ui.app
```

窗口使用固定 Synthetic 场景写入临时 SQLite，并以小型“Mac 测试版”标签标明本地回放范围。普通界面只展示三只候选、当前状态、详情和历史；“模拟数据中断”“恢复回放”和开发诊断保留在“开发”菜单，历史窗口只读，资金模块继续保持未就绪。

## Windows TdxQuant 现场入口

当前单机内部自用优先使用 `StockWatcher-Internal-Portable.zip`：完整解压后双击
**启动 StockWatcher.vbs**。入口复用目标机已允许的 python.org 官方签名 Python 3.12/Pythonw，
不弹控制台、不要求管理员权限、不改 PATH，也不在首次启动联网安装依赖。ZIP 包含完整
`stock_watcher` 应用树、PySide6 UI、原生 Preflight 和冻结 `app/uv.lock`；目标机须提前按该 lock 准备运行依赖，
并可导入与官方终端匹配的 TdxQuant `tqcenter` 模块。只有原生报告整体 `PASS`、恰好一个
`api_session=PASS` 且 `windows_live_verified=true` 时才启动
真实 TdxQuant 诊断 UI，真实字段 M0 完成前候选仍保持关闭。详见包内《第一次使用》。

开发、完整 Preflight、M0 探针和构建仍使用 PowerShell 工程入口：

```powershell
powershell -NoProfile -File .\scripts\windows\stockwatcher.ps1
```

该工程入口可安装/更新环境、执行 TQ 预检、启动安全诊断界面、导出脱敏 M0 报告及构建分发包。便携双击入口与最新参数修复仍待 Human Owner 目标 Windows 独立验证；Mac 离线结果不等于真实 TdxQuant、行情、紫黄线、交易时段或安装体验已验证。详见 [Windows 一页交接](docs/visions/v0.3-windows-data-gate/windows-handoff.md)。

## 版本路线

项目先在 Mac 完成可回放基础与本地 Alpha，再进入真实 Windows/通达信数据闸门，之后接入完整 V1 并稳定化。详见 [版本索引](docs/visions/README.md)；本地与 GitHub 同步规则见 [release.md](docs/process/release.md)。
