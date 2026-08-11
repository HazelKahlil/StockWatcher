# StockWatcher

StockWatcher 是供 2—3 名内部用户使用的 A 股候选观察与异动提醒工具。它从全市场行情中筛出三只值得进一步看盘的候选，并提供固定时点和盘中特别强异动提醒。

> 本项目只提供候选观察和异动提醒，不构成投资建议；不得读取交易密码、连接交易账户或自动下单。

## 当前状态

- 唯一权威工作目录为 `/Users/kahlilhazel/Documents/700-AI-Workspace/20-Projects/StockWatcher`，
  当前日常开发分支为本地 `main`。当前应用代码基线为 `ad04e392...`，Python 版本
  `0.4.0a2`，里程碑 tag 为 `v0.4.0-alpha.2`。
- 该 tag 是 Mac / Web / Windows 的内部试用源码、重建与回滚基准，不是商业稳定发布、
  权威 M0 或完整三平台验收。
- Shared Core 已包含真实全市场扫描、行业与概念板块、板块硬门、稳定 Top3、候选池强异动、
  09:45/14:45 调度、30 天历史、15:30 盘后总结和 SQLite 安全恢复。
- 现有 Mac arm64 App 继续内部使用，但其已记录 `SOURCE_COMMIT=88ccf49f...`，早于 alpha.2；
  本轮没有覆盖、重装或读取 Keychain/运行数据。需要新包时从 tag 重建。
- Windows PR #4 已合并并达到 `WINDOWS_SMOKE_PASS`；Windows 3.11/3.12 CI、Setup、
  PyInstaller/Inno 和制品上传通过，但连续 M0、完整交易日和目标机安装验收仍未完成。
- Web 独立内部测试线固定为 `web/internal-test-v1@bf447ba`，当前由 Mac Docker +
  Cloudflare Tunnel 提供；完整工程门通过且公网当前可达，但状态必须继续为
  **`BLOCKED / NOT_ACCEPTED`**，不得合入 main 或称为生产稳定。
- 普通/历史数据使用内置 Tushare Pro 代理；主实时入口使用
  `tushare.realtime_quote(..., src="sina")`。两者共用系统安全存储中的一个 Token；Mac
  使用系统钥匙串，Windows 使用 Credential Manager。
- 2026-07-31 已在 Mac 交易时段通过原生实时 1/100/300/800、全市场七批、连续双次手动
  Top3 与规则审计；14:45 固定触发及延迟兜底已证明，但新鲜固定时点 Top3 仍待下个交易日。
- 2026-08-01 已构建、ad-hoc 签名并安装本机 arm64 `StockWatcher.app`；全新目录启动、
  macOS 系统钥匙串、SQLite 历史、盘后报告/PDF、单实例唤起、关闭隐藏和显式退出均已实测。
- 2026-07-31 的真实静态收盘回顾已盘后补生成；它只验证确定性收盘分析与 PDF 呈现，
  明确标记为 `RETROSPECTIVE_ONLY`，不冒充 15:30 Live、盘中 Top3 或 Windows 验收。
- TdxQuant 保留为可选诊断和未来资金字段探索，不再是应用正常启动或真实候选的必要前提。
- 资金不可用时显示“资金未确认”且不阻塞候选；日级 moneyflow 不得冒充盘中增强。
- GitHub 私有仓库保留为里程碑镜像、远端备份和交接入口，不承担日常迭代。

## 从这里开始

1. 先阅读 [PROJECT_INDEX.md](PROJECT_INDEX.md) 和 [CURRENT_RELEASES.json](CURRENT_RELEASES.json)。
2. 阅读 [AGENTS.md](AGENTS.md) 与 [docs/00-START-HERE.md](docs/00-START-HERE.md)。
3. 按 [docs/README.md](docs/README.md) 的文档地图恢复项目状态。
4. 先读 [v0.4.0-alpha.2 内部基准](docs/visions/v0.4.0-alpha.2-internal-baseline/README.md)，
   再按需阅读 [Mac V1 当前执行版本](docs/visions/v0.4.2-macos-v1-port/README.md) 与
   [共享连接门返修](docs/visions/v0.4.1-shared-connection-gate/README.md)；Human Owner 的
   Mac-first 决策高于较早 Windows 排期中的冲突表述。
5. 需要 Web 时先读 [Web 轨道](docs/tracks/web.md)，需要 Windows 时先读
   [Windows 轨道](docs/tracks/windows.md)。
6. 开始真实数据工作前，读取 [数据规则](docs/process/rules/data.md) 和安全边界。

## 项目基线

| 项 | 当前约定 |
| --- | --- |
| 当前开发环境 | macOS 本地，Asia/Shanghai |
| 当前 V1 目标环境 | macOS 桌面端；不要求通达信；Windows 后续单独同步与验收 |
| 计划技术栈 | Python 3.11/3.12、PySide6、SQLite WAL、YAML + Pydantic、pytest |
| v0.1 数据口径 | Mock / Replay / Synthetic；不接真实交易账户，不把模拟数据冒充实时行情 |
| V1 主数据口径 | 内置 Tushare Pro 代理 + SDK 原生实时 `src="sina"`；单 Token |
| 默认提醒 | 09:45、14:45；盘中特别强异动最多 3 批/日 |
| 输出 | 数据健康时每批固定三只，标记“强 / 中 / 近” |
| 安全边界 | 不读取账户，不自动交易，不用旧数据伪装正常结果 |

## 本地开发方式

- 日常工作全部在唯一主目录的本地 Git 仓库完成：必要时短分支 → 本地验证 → 本地提交 →
  合并回本地 `main`；不要为小任务复制新的项目目录。
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

## 启动 Mac V1

```bash
uv sync --all-groups --frozen
uv run python -m stock_watcher.ui.app
```

默认入口使用真实 Tushare V1 会话。Mac 首次没有已保存 Token 时会显示简单的“数据接口”
页；Token 只在用户确认后进入系统钥匙串。默认测试与开发可显式选择 Replay，不需要真实
Token。

交易日 09:45 和 14:45 会自动抓取并弹出三只观察股票；盘中任意需要查看的时刻可点击
**立即获取最新3只**，成功后主界面与右下角三只弹窗同步更新。15:30 自动生成全市场
A股盘后回顾，并可从 **设置 → 盘后回顾与PDF** 查看或下载最近31个自然日的固定三页 PDF。

## 构建 Mac 内部测试 App

```bash
uv run pyinstaller --noconfirm --clean \
  --distpath dist/macos-v1 \
  --workpath build/macos-v1 \
  packaging/stockwatcher-macos.spec
codesign --force --deep --sign - dist/macos-v1/StockWatcher.app
codesign --verify --deep --strict dist/macos-v1/StockWatcher.app
```

该 spec 只生成本机架构内部测试包，排除 Windows PowerShell/VBS/Inno、TdxQuant 与 TQ
诊断入口；Token、SQLite、行情缓存、报告和日志均不进入 `.app`。当前不要求 Developer ID
或公证，不能把 ad-hoc 签名写成正式发行签名。

## Mac 数据接口

正常启动不要求通达信或 TQ。打开 **设置 → 数据接口**：

- 在唯一的隐藏 Token 输入框填写 Tushare 数据接口凭据；
- 输入框默认隐藏；测试失败不会替换旧凭据；
- 测试成功并再次确认后才写入 macOS 系统钥匙串；
- Key 更换不需要重装或重新打包；
- 更换 Token 会重新建立实时基线，连续三周期新鲜数据后恢复。

Tushare SDK 原生实时路线与 Pro 代理共用同一 Token，只允许通过受控 Provider 调用；
最多 800 只一批且应用级请求起点间隔默认 1 秒（不得低于 0.6 秒）。核心行情或板块过期时不产生新候选，
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

当前以 `v0.4.0-alpha.2` 固定内部试用基准；后续继续补 Mac、Web 和 Windows 各自未完成的
真实环境门，不互相替代证据。详见 [版本索引](docs/visions/README.md)。
