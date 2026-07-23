# StockWatcher Agent 工作纪律

> 本文件是项目级规则入口，不频繁改动。事实以 workspace 为准，不以聊天记忆为准。

## 项目

- 本地路径：`/Users/kahlilhazel/Documents/700-AI-Workspace/20-Projects/StockWatcher`
- 当前开发电脑：Mac；没有可用于真实验证的 Windows 电脑。
- GitHub：`https://github.com/HazelKahlil/StockWatcher`（private，版本节点镜像，不是日常开发工作区）
- 当前状态：v0.1 Mac Replay Foundation 与 v0.2 Mac Local Alpha 均已本地完成、待同步 GitHub；`v0.3-windows-data-gate` 已激活为双路线真实数据闸门（进行中）。
- 计划技术栈：Python 3.11/3.12、PySide6、SQLite WAL、YAML + Pydantic、pytest、PyInstaller + Inno Setup。
- 当前验证命令：`uv sync --all-groups`、`uv run pytest`、`uv run ruff check .`、`uv run mypy src tests`、`python3 scripts/validate_workspace.py`、`git diff --check`。
- 产品代码落地后必须补齐并执行：`pytest`、lint、类型检查、回放 smoke；命令以 `pyproject.toml` 和活跃版本 README 为准。

## 项目工作流

- 启动读取顺序：本文件 → `docs/README.md` → `docs/project/index.md` → `docs/process/index.md` → `docs/visions/README.md` → 活跃或目标版本 README。
- 真实数据工作进入 v0.3 前，先读取活跃版本 `docs/visions/v0.3-windows-data-gate/README.md`；首个路线执行 issue 进入执行前，必须确认活跃登记已写入本地 `main`。
- 中大型任务动手前先在 `docs/visions/` 锚定版本；没有合适版本先建目录（`v0.x-短名` + `README.md`）。
- 按版本推进：明确范围 → 实现 → 验证 → 更新版本记录 → 封版或留下有 owner 的下一步。
- 长任务跨 session 时按 `kahlil-project-workflow` 的 handoff 规则交接；小改动优先更新目标版本 README，不滥建 session log。
- 文档去向：长期事实 → `docs/project/`；规则、边界、决策和踩坑 → `docs/process/`；范围、验收、封版 → `docs/visions/`；原始交接基线 → `docs/reference/`。

## 本地优先模式

- 本地 `main` 是日常开发的权威事实源；`origin/main` 是最近一次 GitHub 里程碑镜像。每次启动先运行 `git status -sb` 和 `git rev-list --left-right --count main...origin/main`，确认本地改动与未同步提交。
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
- 紫黄线、供应商字段、批量能力与授权必须通过对应路线 M0。Tushare 字段不得命名或展示为通达信紫黄线；独立资金字段 M0 未通过时只能明确标记“资金模块未就绪”，不得用替代字段冒充。
- Mac 的 Tushare Pro 真实行情 M0 只能使用供应商正式支持的 HTTPS POST/JSON；无法正式确认 HTTPS 时该路线直接 FAIL，真实 token 不得经 HTTP 传输。
- Tushare 只使用 Human Owner 自有且已授权的账号；token 只能从 `TUSHARE_TOKEN` 环境变量读取，不得进入 issue、评论、日志、Git、SQLite、截图或示例配置。Agent 不注册、不购买、不接受条款、不回显 token。
- Mac 上的 Mock/Replay、PySide6 和性能结果只证明 Mac 本地行为，不能充当 Windows/通达信 M0、Windows 通知或安装包证据。
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
