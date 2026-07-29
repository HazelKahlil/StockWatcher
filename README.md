# StockWatcher

StockWatcher 是供 2—3 名内部用户使用的 A 股候选观察与异动提醒工具。它从全市场行情中筛出三只值得进一步看盘的候选，并提供固定时点和盘中特别强异动提醒。

> 本项目只提供候选观察和异动提醒，不构成投资建议；不得读取交易密码、连接交易账户或自动下单。

## 当前状态

- 项目治理与 V2.0 交接基线已建立。
- 当前活跃目标是 `v0.4-v1-feature-complete`：在 Windows 全市场持续选出稳定三只真实
  A 股，并完成固定时点与强异动提醒、详情、30 天历史和收盘总结。
- 普通/历史数据使用内置 Tushare Pro 代理；主实时入口使用
  `tushare.realtime_quote(..., src="sina")`。两者共用 Windows Credential Manager 中
  的一个 Token。
- 候选链路已经接入生产入口；交易时段 30 分钟、真实固定提醒、实机截图与安装包启动证据
  仍是发布前严格验收门。
- 2026-07-29 已使用当日真实收盘数据完成盘后回顾测试和总结视图；该证据只验证日线
  回溯与呈现，不代替 Human Owner 后续安排的连续 30 分钟交易时段验收。
- TdxQuant 保留为可选诊断和未来资金字段探索，不再是应用正常启动或真实候选的必要前提。
- 资金不可用时显示“资金未确认”且不阻塞候选；日级 moneyflow 不得冒充盘中增强。
- GitHub 私有仓库保留为里程碑镜像、远端备份和交接入口，不承担日常迭代。

## 从这里开始

1. 阅读 [AGENTS.md](AGENTS.md)。
2. 按 [docs/README.md](docs/README.md) 的文档地图恢复项目状态。
3. 阅读 [V1 当前执行版本](docs/visions/v0.4-v1-feature-complete/README.md)；2026-07-29
   Human Owner 交接包高于旧 V2.0 中冲突的范围。
4. 阅读 [数据规则](docs/process/rules/data.md) 和安全边界。

## 项目基线

| 项 | 当前约定 |
| --- | --- |
| 当前开发环境 | Windows 本地，Asia/Shanghai |
| V1 目标环境 | Windows 桌面端；不要求通达信 |
| 计划技术栈 | Python 3.11/3.12、PySide6、SQLite WAL、YAML + Pydantic、pytest |
| v0.1 数据口径 | Mock / Replay / Synthetic；不接真实交易账户，不把模拟数据冒充实时行情 |
| V1 主数据口径 | 内置 Tushare Pro 代理 + SDK 原生实时 `src="sina"`；单 Token |
| 默认提醒 | 09:45、14:45；盘中特别强异动最多 3 批/日 |
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

## 启动 Windows V1

```bash
uv sync --all-groups --frozen
uv run python -m stock_watcher.ui.app
```

默认入口使用真实 Tushare V1 会话。没有已保存 Token 时，打开 **设置 → 数据接口**；
默认测试与开发可显式选择 Replay，不需要真实 Token。

## Windows 数据接口

正常启动不要求通达信或 TQ。打开 **设置 → 数据接口**：

- 在唯一的隐藏 Token 输入框填写 Tushare 数据接口凭据；
- 输入框默认隐藏；测试失败不会替换旧凭据；
- 测试成功并再次确认后才写入 Windows Credential Manager；
- Key 更换不需要重装或重新打包；
- 更换 Token 会重新建立实时基线，连续三周期新鲜数据后恢复。

Tushare SDK 原生实时路线与 Pro 代理共用同一 Token，只允许通过受控 Provider 调用；
最多 800 只一批且批次起始间隔不少于 0.5 秒。核心行情或板块过期时不产生新候选，
保留上次三只并标记数据延迟；资金缺失只降级资金状态。

不要把凭据写入命令行、配置、日志、SQLite 或仓库。真实测试使用显式
`pytest -m live_tushare`，默认测试不需要 Key 或外网。

## Windows TdxQuant 可选诊断

官方 TdxQuant 诊断仍保留严格签名发现、Preflight 和只读 UI。它只用于历史证据复核与未来
资金字段探索；未通过独立诊断门时不会影响 Tushare 正常启动，也不会自动启动终端。

开发、完整 Preflight、M0 探针和构建仍使用 PowerShell 工程入口：

```powershell
powershell -NoProfile -File .\scripts\windows\stockwatcher.ps1
```

该工程入口可安装/更新环境、执行 TQ 预检、启动安全诊断界面、导出脱敏 M0 报告及构建分发包。便携双击入口与最新参数修复仍待 Human Owner 目标 Windows 独立验证；Mac 离线结果不等于真实 TdxQuant、行情、紫黄线、交易时段或安装体验已验证。详见 [Windows 一页交接](docs/visions/v0.3-windows-data-gate/windows-handoff.md)。

## 版本路线

当前先完成 Windows V1 的真实交易时段和安装验收，结束后停止并等待评审，不启动 Mac。
详见 [版本索引](docs/visions/README.md)。
