# v0.6：次日同点复盘

> 状态：本地代码完成并通过 macOS 离线工程门；真实交易时段同点行情待 Human Owner 验收，未同步 GitHub。
> 基线：`v0.4.0-alpha.2` / `main@e8df598c920a15dfb565bfc75e45a93d1eb48a07`
> 范围：Shared Core 与 Mac/Windows 共用桌面端；不修改 Web 独立测试线。

## 目标

为正式 `scheduled-09:45` 与 `scheduled-14:45` 三只候选增加旁路“次日同点复盘”：
当日入选价作为理论入场价，下一真实交易日相同档位行情作为理论离场价。功能只做历史复盘
与统计，不连接交易账户、不下单、不改变评分、StableTop3、行业门、提醒状态机或强异动。

## 锁定口径

- 单笔收益率：`(exit_price / entry_price - 1) × 100`。
- `win/loss/flat` 仅来自已结算记录；`pending/unavailable` 不进入胜率分母。
- 近 1 周/近 1 月分别为最近 5/20 个有入选记录的交易日，不按自然日。
- 日组合胜率只统计同一入选交易日 09:45 与 14:45 共六笔全部结算的日期。
- 同股双档是两笔；连续交易日同档同股先结算旧笔，再创建新笔。
- 交易日必须来自验证过的开市日历；不能以工作日猜测节假日。

## 数据与结算优先级

1. 复用下一交易日固定全市场扫描中的真实行情。
2. 对缺失项最多三只执行一次批量 `realtime_quotes`。
3. 错过时点或实时失败后，串行使用 `stk_mins` 对应 09:45/14:45 一分钟 close 回补。

价格小于等于零、错日、缺源时间、过期、质量不足、停牌或无成交均不结算为亏损；记录为
`pending` 或 `unavailable`。网络请求与 SQLite 事务严格分离，复盘异常不得影响固定扫描、
Top3、提醒或弹窗。

固定扫描完成后只把旧笔结算排入独立单线程旁路：先复用该轮全市场行情，缺失时最多三只
做一次批量实时补查；若仍待结算，则在目标分钟闭合一分钟后安排有界历史回补。日历、批量
补查、分钟回补和 SQLite 复盘写入均不在固定提醒等待路径中。

## Schema

- 当前基线：SQLite v6。
- 目标：SQLite v7，新增独立 `candidate_outcomes`。
- 迁移沿用迁移前备份、事务回滚、迁移后 `integrity_check` 与只读降级。
- 复盘记录至少保留一年，不随 31 天提醒历史清理删除。

## UI

现有“历史记录”窗口增加“提醒记录 / 次日复盘”标签；复盘页默认近 1 月，可切换近 1 周、
近 1 月、全部，展示总体与 09:45/14:45 分档统计、价格、收益、状态和结算方式。上涨红、
下跌绿、待结算或不可用灰；数据库读取继续在工作线程执行。

旧 `docs/process/rules/ui.md` 禁止普通候选页使用“买入/卖出/胜率”避免收益承诺；本版本由
Human Owner 明确授权一个有边界的复盘例外，只在“理论复盘”页面展示历史统计，并固定显示
“未计手续费、滑点及实际成交限制，不构成投资建议”。普通候选与提醒文案不变。

## 验收门

- [x] v7 迁移、CRUD、幂等、统计、保留与只读降级。
- [x] 固定提醒自动创建 pending，下一交易日同档自动结算。
- [x] 可靠交易日历、一次批量实时与串行分钟回补。
- [x] 最近 30 天可验证 scheduled 历史回补；manual、intraday、Replay、Synthetic 和补位候选排除。
- [x] 历史窗口复盘 UI 的 empty/pending/partial/complete 状态与后台读取。
- [x] 复盘故障或慢请求不影响原 Top3/提醒的旁路回归。
- [x] 全量 pytest、Ruff、Mypy、workspace validator、Windows package contract、lock 与 diff 门。
- [x] 仓库外脱敏交接 ZIP 与三态 UI 截图。
- [ ] 下一真实交易日 09:45/14:45 的现场同点结算与错过时点回补验收。

## 离线验证（macOS arm64，2026-08-11）

| 命令 | 结果 |
| --- | --- |
| `uv sync --all-groups --frozen` | exit 0；56 packages audited |
| `uv lock --check` | exit 0；67 packages resolved |
| `uv run pytest -m 'not live_tushare' -ra -o addopts=''` | exit 0；379 passed，20 skipped，2 deselected |
| `uv run ruff check .` | exit 0 |
| `uv run mypy src tests` | exit 0；113 source files |
| `python3 scripts/validate_workspace.py` | exit 0；29 required files |
| `uv run python scripts/check_windows_package.py` | exit 0；仅离线跨平台契约，不冒充 Windows 真机 |
| `git diff --check` | exit 0 |

确定性 UI 已覆盖空数据、待结算、部分结算和完整结算；交接包保存 empty / pending /
settled 三张实拍截图。未构建或覆盖现有 Mac/Windows App。

## 本地提交与同步

- 实现分支：`feat/candidate-outcomes`；完成后按 local-first 规则合入本地 `main`。
- 实现提交：`c7b4f9989a298954f3127934b7570afb3f5aaf2b`；最终提交列表同时写入仓库外交接包。
- GitHub：未 push、未创建 PR、未发布 Release；`origin/main` 仍是 `v0.4.0-alpha.2` 节点。

## 证据边界

本轮不读取 Token、Keychain、Credential Manager、用户真实数据库、运行日志或交易账户，
不覆盖已安装 App，不 push、不建 PR、不发布 Release。非交易时段的代码、回放、迁移和 UI
通过不能冒充真实次日同点行情验收；该现场项在完成后继续明确列给 Human Owner。
