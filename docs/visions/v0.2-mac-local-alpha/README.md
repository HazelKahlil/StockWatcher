# v0.2-mac-local-alpha：交付基于 Replay 的 Mac 本地 Alpha

> 状态：本地完成，待同步
> 创建：2026-07-22 ｜ 计划 tag：`v0.2.0`

## 目标与范围

- 在 Mock/Replay 数据上实现股票池与排除、价格/涨速、板块共振、三日趋势、固定三只和“强/中/近”。
- 实现健康状态、SQLite 基础、模拟 09:45/14:50 调度、提醒状态机和 Mac 可运行的 PySide6 本地界面。
- UI 和所有输出必须醒目标明“模拟/回放数据”，资金模块显示 unavailable。
- 不接真实通达信、紫黄线或交易账户；不交付 Windows 通知、多屏验收、iPhone、正式安装包或自动调参。

## 依赖

- v0.1 工程、Provider Protocol、Replay/Synthetic 和验证命令已在本地封版。
- 执行 issue：`HAZ-392` 已于 2026-07-23 建立开工门；HAZ-400 从 HAZ-399 基线 `cd91df0f57be3eb5f6609308804632011bff4d98` 建立独立实现分支。
- UI 运行依赖 `PySide6`；版本、许可证和安全影响见 `docs/process/dependencies.md`。启动命令为 `uv run python -m stock_watcher.ui.app`。
- 后续实现必读：`docs/process/rules/ranking.md`、`docs/process/rules/data.md`、`docs/process/rules/ui.md`、`docs/process/rules/storage.md`、`docs/process/rules/security.md` 和 `docs/process/boundaries.md`。
- 本版本的 Mac + Mock/Replay 结果只证明本地行为；真实通达信、紫黄线、Windows 行为和安装包均未验证。GitHub 尚未同步本地 v0.1 基线与本开工登记。

## 验收标准

- [x] 锁定股票池排除项有单元测试；0/1/2/3+ 完整信号场景的固定三只逻辑正确。
- [x] 相同 Replay 输入与配置得到完全相同的候选、等级、原因和提醒事件。
- [x] 价格很强但板块弱最多为“近”；核心数据断开时停止新候选并正确预热恢复。
- [x] 模拟 09:45/14:50、同股冷却、无变化不重复、三四名防抖和每日上限通过回放。
- [x] Mac 界面能展示模拟健康、当前 Top3、详情与历史；不会使用“买入/卖出/胜率”等表达。
- [x] SQLite、日志、配置版本和健康指标可追溯，未保存凭证。
- [x] README 和 UI 清楚列出真实数据、紫黄线、Windows 行为均未验证。

## 进度

| 日期 | 进展 | 状态 |
| --- | --- | --- |
| 2026-07-22 | 从原 M1 Alpha 中拆出纯 Mac/Replay 可验证范围 | 计划中 |
| 2026-07-23 | HAZ-392 激活开工门：v0.2 登记为本地 `main` 活跃版本，基线为 `cc0a4d2`；Mac + Mock/Replay 边界及 GitHub 未同步事实已确认 | 进行中 |
| 2026-07-23 | HAZ-393 完成 Replay 确定性候选、提醒策略与 SQLite 可追溯核心；UI 与真实数据验证仍未开始 | 进行中 |
| 2026-07-23 | HAZ-395 修复替代关系/新鲜批次防抖、固定时点与盘中额度分账，并完成 v1→v2 SQLite 原子迁移与回归 | 进行中 |
| 2026-07-23 | HAZ-397 仅修复替换关系中断后防抖状态清空，并补回放序列回归；尚未获得新的整体复审结论 | 进行中 |
| 2026-07-23 | HAZ-400 完成 Mac PySide6 Replay UI 纵切：主窗口、健康安全状态、固定三行弹窗、详情、历史只读、固定 Synthetic smoke；待 Human Visual Acceptance | 实现完成，待验收 |
| 2026-07-23 | HAZ-401 按第一次 Human Visual Acceptance 简化普通用户视图：中文三卡主窗口、固定三行提醒、数据中断旧结果降权、交易员可读详情、技术字段移入开发菜单；五张 Mac + Mock/Replay 新截图待第二次视觉验收 | 已实现，待验收 |
| 2026-07-23 | HAZ-398 对核心提交 `fc62396f47b0c7cf535692e20639a75b2c56ea73` 给出 PASS；HAZ-399 已将该核心以 fast-forward 合入本地权威 `main`，并在随后的治理提交形成可回读基线。`uv sync --all-groups --frozen`、`uv lock --check`、pytest、Replay smoke、Ruff、Mypy、workspace validation 与 `git diff --check` 验证结果见本工单交付记录 | 进行中 |
| 2026-07-23 | HAZ-400/HAZ-401 的 Mac Replay UI 已经第二次 Human Visual Acceptance 通过，并以 fast-forward 合入本地 `main`；本地完整门禁复跑通过，v0.2 的 Mac + Mock/Replay 范围收口 | 本地完成，待同步 |

## 决策与风险

| 决策/风险 | 理由/应对 |
| --- | --- |
| Alpha 只使用 Mock/Replay | 先验证产品流程和规则，不制造不可靠的数据接入 |
| Mac UI 证据不继承到 Windows | v0.3/v0.5 必须重新验证窗口行为、通知和打包 |
| UI 证据只来自真实 Mac 图形会话和固定 Synthetic 数据 | 不把 Mac 结果外推为 Windows、通达信、紫黄线或安装包证据 |

## Session Handoff 索引

尚无。

## 本地完成记录

- Human Visual Acceptance：2026-07-23 第二次验收已通过；证据为 HAZ-401 的五张真实 macOS + Mock/Replay 截图（主窗口、固定三只提醒、数据中断、详情、历史）。该验收仅覆盖已展示的 Mac Replay 视觉基线。
- 验证：在 macOS 上执行 `uv sync --all-groups --frozen`、`uv lock --check`、`uv run pytest`（38 passed）、离屏 Replay/UI smoke（五张状态图均成功生成）、`uv run ruff check .`、`uv run mypy src tests`、`python3 scripts/validate_workspace.py` 与 `git diff --check`，均通过。
- 合入：HAZ-400 UI `4f6e424db8ab99ecd0f3610c96f043cc446b8852` 与 HAZ-401 视觉修订 `fe2caeefa951a7cc51eadd02d0b3cb648d127ef5` 已以 fast-forward 进入本地 `main`；未改写历史。
- 环境边界：本版本只证明 macOS + Mock/Replay。本地结果不证明 Windows、通达信、真实行情、紫黄线、Windows 通知、多屏、安装包或目标环境 UAT；资金模块保持未就绪。真实数据闸门由 v0.3 承接。
- 同步状态：本地 `main` 是权威；GitHub 尚未同步，未创建 `v0.2.0` tag。
