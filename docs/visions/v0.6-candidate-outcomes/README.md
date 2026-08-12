# v0.6：次日同点复盘

> 状态：`local_code_complete_offline_verified_after_alpha4_reliability_fix`；真实交易时段同点行情待 Human Owner 验收，未同步 GitHub。
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

### 真实交易日历质量契约

- 只接受受控 `tushare_15000` 普通 Pro `trade_cal` 路线。Provider 包装层创建的逻辑
  endpoint 仍为 `/`，SDK Pro 传输层改写后的 provenance wire endpoint 必须精确为
  `/trade_cal`；字段必须精确为 `exchange/cal_date/is_open/pretrade_date`。
- 允许该路线因供应商不提供生成时间而产生的
  `DEGRADED + source_ts missing/received-fallback`；`received_ts` 必须为合法的
  aware datetime。
- 每条 `cal_date`、`is_open`、请求区间、重复日期状态和 exchange 均严格校验；`is_open`
  只接受数字 0/1 或明确布尔值，不接受字符串；空响应、
  越界、矛盾、schema 变化、STALE/STOPPED、网络、限流或鉴权失败继续 fail closed。
- 下一交易日只来自真实开市记录，绝不按星期一到星期五猜测节假日。

### 分钟回补重试状态机

- `backfill_due` 必须指定 `target_trade_date + target_slot + limit`，09:45 与 14:45
  各自最多处理三笔；旧积压不能占用当前档位名额。
- 当前交易日按目标分钟后 `+1 / +3 / +8 / +20 / 15:05 最终确认` 有界退避；网络、
  限流、服务器错误和首次空数据保持 pending，不高频重试。
- 仅收盘后最终确认精确分钟仍不存在或明确无成交时标为 unavailable；过去日期的 30 天
  历史回补使用最终确认语义。
- 尝试次数、上次尝试和下次重试时间持久化到 SQLite；App 重启后从数据库重新发现 due
  pending。队列任务使用实际执行时刻，超过 500 笔积压时仍优先发现当前目标档位。

## Schema

- 原候选复盘基线：SQLite v7。
- 当前：SQLite v8；在独立 `candidate_outcomes` 上新增
  `settlement_attempts/last_attempt_at/next_retry_at`。
- 迁移沿用迁移前备份、事务回滚、迁移后 `integrity_check` 与只读降级。
- v7 → v8 migration 已覆盖失败回滚和 Windows 文件句柄释放契约。
- 本次 wire endpoint 返修不修改表、索引或重试字段，Schema 继续保持 v8。
- 复盘记录至少保留一年，不随 31 天提醒历史清理删除。

## UI

现有“历史记录”窗口增加“提醒记录 / 次日复盘”标签；复盘页默认近 1 月，可切换近 1 周、
近 1 月、全部，展示总体与 09:45/14:45 分档统计、价格、收益、状态和结算方式。上涨红、
下跌绿、待结算或不可用灰；数据库读取继续在工作线程执行。

旧 `docs/process/rules/ui.md` 禁止普通候选页使用“买入/卖出/胜率”避免收益承诺；本版本由
Human Owner 明确授权一个有边界的复盘例外，只在“理论复盘”页面展示历史统计，并固定显示
“未计手续费、滑点及实际成交限制，不构成投资建议”。普通候选与提醒文案不变。

## 验收门

- [x] v7 → v8 迁移、CRUD、幂等、统计、保留、失败回滚与只读降级。
- [x] 固定提醒自动创建 pending，下一交易日同档自动结算。
- [x] 受控 DEGRADED 真实交易日历契约、一次批量实时与有界串行分钟回补。
- [x] 最近 30 天可验证 scheduled 历史回补；manual、intraday、Replay、Synthetic 和补位候选排除。
- [x] 历史窗口复盘 UI 的 empty/pending/partial/complete 状态与后台读取。
- [x] 复盘故障或慢请求不影响原 Top3/提醒的旁路回归。
- [x] 全量 pytest、Ruff、Mypy、workspace validator、Windows package contract、lock 与 diff 门。
- [x] 仓库外脱敏交接 ZIP 与三态 UI 截图。
- [ ] 下一真实交易日 09:45/14:45 的现场同点结算与错过时点回补验收。

真实生产链回归使用 fake `requests.Session` 和内存测试凭据，不联网、不读取真实 Token，并
完整经过 `Tushare15000Provider → TushareSdkProTransport → BaseHttpTransport →
ProviderProvenance → CandidateOutcomeTracker`。测试同时断言逻辑 endpoint `/`、wire URL
`/trade_cal`、DEGRADED/MISSING provenance 与下一真实开市日解析；错误 endpoint/profile、
字段漂移、非受控 DEGRADED、STALE、空记录、越界和矛盾记录全部拒绝。

## 离线验证（macOS arm64，2026-08-12）

| 命令 | 结果 |
| --- | --- |
| `uv sync --all-groups --frozen` | exit 0；56 packages audited |
| `uv lock --check` | exit 0；67 packages resolved |
| `uv run pytest -m 'not live_tushare' -ra -o addopts=''` | exit 0；417 passed，20 skipped，2 deselected |
| `uv run ruff check .` | exit 0 |
| `uv run mypy src tests` | exit 0；113 source files |
| `python3 scripts/validate_workspace.py` | exit 0；29 required files |
| `uv run python scripts/check_windows_package.py` | exit 0；仅离线跨平台契约，不冒充 Windows 真机 |
| `git diff --check` | exit 0 |
| `python3 -m json.tool CURRENT_RELEASES.json` | exit 0 |

确定性 UI 已覆盖空数据、待结算、部分结算和完整结算；交接包保存 empty / pending /
settled 三张实拍截图。未构建或覆盖现有 Mac/Windows App。

## 本地提交与同步

- 原功能提交：`c7b4f9989a298954f3127934b7570afb3f5aaf2b`。
- 交易日历质量与重试返修提交：`d85f378`。
- wire endpoint 返修分支：`fix/candidate-outcomes-trade-cal-wire-endpoint`；实现提交：
  `e85faf2d83b2a8877a6320dfd4b60ae9e780486d`。
- alpha.4 P1 可靠性返修分支：`fix/candidate-outcomes-p1-reliability`；实现提交：
  `ed63106c467637c3518df7821b2c32ae3c683ab7`。验证完成后按 local-first 规则合入本地 `main`。
- GitHub：未 push、未创建 PR、未发布 Release；`origin/main` 仍是 `v0.4.0-alpha.2` 节点。

## 证据边界

本轮不读取 Token、Keychain、Credential Manager、用户真实数据库、运行日志或交易账户，
不覆盖已安装 App，不 push、不建 PR、不发布 Release。非交易时段的代码、回放、迁移和 UI
通过不能冒充真实次日同点行情验收；该现场项在完成后继续明确列给 Human Owner。
