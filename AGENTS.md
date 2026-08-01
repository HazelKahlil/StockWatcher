# StockWatcher Agent 工作纪律

> 本文件是项目级规则入口，不频繁改动。事实以 workspace 为准，不以聊天记忆为准。

## 项目

- 本地路径：由当前恢复工作区决定；冻结 Windows 源基线为
  `5b20b707e83baa16b1486894f8e53f343830d67c`。
- 当前开发电脑：Mac。真实行情、通知、安装和截图证据必须标明本机 macOS；不能外推为
  Windows 结果。
- GitHub：`https://github.com/HazelKahlil/StockWatcher`（private，版本节点镜像，不是日常开发工作区）
- 当前状态：共享连接门返修已作为 `74b4840d25766097a2c88e502983b375bc80c7d6`
  独立提交；Mac 专属工作位于 `feat/macos-v1-port`。Windows 真实验收仍为 `FAIL`：
  统一 Token 连接校验遇到 `rate_limited`，完整扫描轮次和真实 Top3 均为 0。本轮禁止
  访问或修改远端。
- 计划技术栈：Python 3.11/3.12、PySide6、SQLite WAL、YAML + Pydantic、pytest、PyInstaller + Inno Setup。
- 当前验证命令：`uv sync --all-groups`、`uv run pytest`、`uv run ruff check .`、`uv run mypy src tests`、`python3 scripts/validate_workspace.py`、`git diff --check`。
- 产品代码落地后必须补齐并执行：`pytest`、lint、类型检查、回放 smoke；命令以 `pyproject.toml` 和活跃版本 README 为准。

## 项目工作流

- 启动读取顺序：本文件 → `docs/README.md` → `docs/project/index.md` → `docs/process/index.md` → `docs/visions/README.md` → 活跃或目标版本 README。
- Mac 真实数据工作先读取活跃版本
  `docs/visions/v0.4.2-macos-v1-port/README.md`，再读取共享返修
  `v0.4.1-shared-connection-gate`；Windows 真实验收历史继续参考
  `v0.4-v1-feature-complete`，TdxQuant 只读历史与诊断参考 `v0.3-windows-data-gate`。
- 中大型任务动手前先在 `docs/visions/` 锚定版本；没有合适版本先建目录（`v0.x-短名` + `README.md`）。
- 按版本推进：明确范围 → 实现 → 验证 → 更新版本记录 → 封版或留下有 owner 的下一步。
- 长任务跨 session 时按 `kahlil-project-workflow` 的 handoff 规则交接；小改动优先更新目标版本 README，不滥建 session log。
- 文档去向：长期事实 → `docs/project/`；规则、边界、决策和踩坑 → `docs/process/`；范围、验收、封版 → `docs/visions/`；原始交接基线 → `docs/reference/`。

## 本地优先模式

- 本地工作区是本轮权威事实源。2026-07-30 Mac-first 任务期间不得 fetch、pull、push、
  merge、tag、release 或访问 GitHub，也不得用远端状态改变已核对基线。
- 日常任务从本地 `main` 建短分支，完成验证和 diff review 后本地合并回 `main`。不自动 push，也不为每个小改动创建 PR。
- 每个 session 收尾必须形成可恢复的本地提交；只留未提交工作时，必须在 handoff 中逐项列出，不能让下一位 Agent 猜。
- 版本封版、需要远端备份/跨设备交接或用户明确要求时，从本地 `main` 创建 `publish/<version>`，统一 push 并用一个 PR 同步 GitHub。
- GitHub 尚未同步期间，不得声称远端已经包含本地版本；汇报必须同时写明本地 commit 和远端同步状态。

## 规格与材料规则

- `docs/reference/v2.0/requirements.lock.json` 是 V2.0 锁定业务项。不得在实现中静默覆盖；业务变更必须由业务负责人确认并形成新版本记录。
- `docs/reference/v2.0/SPEC_V2.0_AGENT.md` 是当前可检索规格基线。交接包中的 DOCX 与 ZIP 因内容重复未入库，选择依据和源哈希见同目录 `README.md`。
- 不把 `docs/reference/` 里的示例配置或 SQL 直接视为已验证生产配置；先在目标版本中确认、测试，再提升到正式工程路径。
- 不迁移 Downloads 中的重复副本，也不把运行数据、密钥、数据库、日志或安装包提交到 Git。

## 不可突破的边界

- 只做候选观察与异动提醒；禁止读取交易密码、连接交易账户、调用下单接口或生成自动交易行为。
- 实时筛选必须是确定性、可回放的规则；大语言模型不得进入盘中主链路。
- 2026-07-29 Human Owner 最终确认 V1 主路线：普通/历史使用内置 Pro 代理
  `https://fastapic.stockai888.top`；主实时使用
  `tushare.realtime_quote(..., src="sina")`，校验地址
  `https://realtime.stockai888.top`；两者共用一个 Token。旧 Super、Fast 命名路线与
  TdxQuant 仅保留高级诊断。
- TdxQuant 保留为可选诊断和资金字段实验；不得成为正常启动的必要前提。
- 新供应商响应必须先归一化；同一候选批次不得拼接不同来源的同类实时字段。
- Human Owner 于 2026-07-29 明确授权使用 Tushare SDK
  `realtime_quote(src="sina")` 原生实时路线。它是生产主实时入口；不得扩展为任意
  网页抓取，也不得与其他来源的同类实时字段拼接。
- 凭据正式运行优先使用 Windows Credential Manager / macOS Keychain；不得进入配置、
  SQLite、日志、Git、bundle、截图或命令行。
- TdxQuant 只读预检可使用官方 `tqcenter` 或 `http://127.0.0.1:17709/`；不得把本机 HTTP 误写成供应商托管 HTTPS，也不得开放到非回环地址。
- V1 不画紫黄线。moneyflow 和其他资金字段必须证明盘中持续更新后才能参与盘中评分；
  日级数据只可作背景，缺失时标记“资金未确认”、计 0 分且不得阻塞候选。
- Mac 上的 Mock/Replay、PySide6 和性能结果只证明 Mac 本地行为；Mac 的真实 Tushare
  结果也不能充当 Windows/通达信 M0、Windows 通知或安装包通过证据。
- 数据健康为 `STOPPED/RED` 时停止产生新候选；不得把旧数据包装成新结果。
- 高风险区和人类确认门见 `docs/process/boundaries.md`。命中时先停、说明影响并取得确认。

## 领域规则

涉及代码修改时，先查 `docs/process/index.md` 的规则路由表并阅读命中文件。违反领域硬规则按 review P1 处理。若规则与 `requirements.lock.json` 冲突，以锁定业务项为准并记录冲突，不自行改需求。

## Git、PR 与发布

- 本地分支名：`feat/<issue>-<slug>`、`fix/<issue>-<slug>`、`docs/<issue>-<slug>`、`chore/<issue>-<slug>`；本地验证后合并回本地 `main`。
- GitHub 同步分支统一用 `publish/<version>`。一个版本原则上只开一个同步 PR，减少日常 GitHub 操作。
- 里程碑 PR 必须写清版本范围、验证证据、环境边界、数据/安全影响和文档更新；模板见 `.github/pull_request_template.md`。
- 采用 SemVer；版本路线、封版门和 tag 规则见 `docs/process/release.md`，变更同步 `CHANGELOG.md`。

## 开发前后检查

- 动手前：读活跃版本 README 和规则路由表；确认任务在版本范围内、依赖已满足。
- 提交前：跑目标版本要求的测试、lint、类型检查和回放；执行 `git diff --check`，review diff，确认无秘密、运行数据、无关改动和跨环境误报。
- 收尾时：更新版本进度、验证证据和风险；新决策或复发坑写回 `docs/process/`。
- 把 issue、父工作包或版本推到终态前，逐项核对 Done when、必需子任务/stage 与版本验收。仍有必要项未完成或无 owner 的欠账时保持 `in_progress` 或 `blocked`；代码合并不等于版本完成。
- local-first 期间，治理规则只有合入本地 `main` 并能从新的本地 worktree 回读才算对日常开发生效；只有完成里程碑 PR 后，才能宣称 GitHub 已同步。

## Review 规则

- 文档和局部低风险改动：自检 + 可复现验证证据。
- 数据源、排名、提醒状态机、持久化、接口契约、权限或封版：结构化 review（P0/P1/P2），并明确回放/真实环境证据。
- 任何自动交易、凭证泄露、未来数据泄漏、数据中断仍发候选，均为 P0，必须阻断合入。
