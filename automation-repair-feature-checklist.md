# StockWatcher Automation Repair Feature Checklist

状态：20 项代码能力均已在短分支 `fix/automation-reliability-audit` 核对；完整工程门、SQLite 迁移、离线演练、冷启动、内部 App 和人工审计均已形成证据。下一交易日 Mac Live 验收仍未完成，不能据此宣称完整交易日通过或 Windows 通过。

应用提交：`8582c9060e4011289c1e521e9d77d7726b05102f`（返修提交 `7baa0e52016055126bb031f6c99f8aa2c69923a9` 的 cherry-pick）
后续最小修复：`904a8d5a8e21565bac22bcf126d0eead34013238`（Ruff/Mypy）；`a15e6766631d03e72de4dd9db31379b0b6fdb28d`（PyInstaller seed 路径）。

| # | 要求 | 代码入口 | 当前核对 |
|---:|---|---|---|
| 1 | `automation_tasks` 持久任务表 | `src/stock_watcher/storage/sqlite.py`：`_apply_v4_migration()`、`ensure_automation_task()`、`update_automation_task()` | 已具备；v3→v4 隔离迁移和真实 App 迁移均通过 |
| 2 | `scan_runs` 每轮扫描审计表 | `src/stock_watcher/storage/sqlite.py`：`_apply_v4_migration()`、`record_scan_run()`；`src/stock_watcher/ui/tushare_v1_session.py`：`_record_scan_run()`、`_record_scan_skip()` | 已具备；健康轮和错误轮离线演练通过 |
| 3 | 09:45、14:45、15:30 确定性 task key | `src/stock_watcher/runtime/automation.py`：`AutomationTaskType`、`AutomationPlanner.for_date()`、`task_key()` | 已具备 |
| 4 | fixed task `planned/running/succeeded/failed` | `src/stock_watcher/runtime/automation.py`：`AutomationTaskState`；`src/stock_watcher/ui/tushare_v1_session.py`：`_mark_task_running()`、`_mark_task_succeeded()`、`_fail_fixed_task()` | 已具备；四态和 attempts 离线演练通过 |
| 5 | 失败原因与 attempts | `src/stock_watcher/storage/sqlite.py`：`attempts/detail` 字段与更新；`tushare_v1_session.py`：失败记录路径 | 已具备；网络、Token、股票池、扫描、deadline 错误均可见 |
| 6 | 真实扫描不等待能力探针全绿 | `src/stock_watcher/ui/tushare_v1_session.py`：能力探针分支（约 503–522 行） | 已具备；unknown/rate-limited 固定扫描演练通过 |
| 7 | stale-but-usable 静态缓存仍可实时扫描 | `src/stock_watcher/runtime/universe_cache.py`：`maximum_degraded_age`、`universe_is_usable()`；`tushare_v1_session.py`：current/usable 分支 | 已具备；stale-but-usable 注入时钟演练通过 |
| 8 | 静态刷新不清空分钟缓冲与稳定 Top3 | `src/stock_watcher/runtime/tushare_runtime.py`：`prepare()` 的 cold-start 与 routine refresh 分支 | 已具备；回归测试通过 |
| 9 | concept failure 原因与后台重试 | `src/stock_watcher/runtime/tushare_runtime.py`：`last_concept_failure`、`_concept_memberships()`；`tushare_v1_session.py`：`_poll_universe_refresh()` | 已具备；失败原因和后台重试演练通过；当前真实 seed 仍 `concept_loaded=false`，概念 Live 尚未通过 |
| 10 | 15:30 本地总结兜底 | `src/stock_watcher/ui/tushare_v1_session.py`：`_execute_summary_task()`、`_generate_summary()`；`src/stock_watcher/engine/daily_summary.py` | 已具备；SQLite/JSON/Markdown fallback 演练通过 |
| 11 | `CandidateSelectionAudit` | `src/stock_watcher/engine/candidates.py`：`CandidateSelectionAudit`、`build_selection_audit()` | 已具备；SQLite 导出通过 |
| 12 | raw Top20、raw Top3、stable Top3 | `candidates.py`：审计 rows 前 20、`raw_codes`、`stable_codes`；`tushare_runtime.py`：扫描结果落审计 | 已具备；导出包含 scan-runs 与 candidate-audit |
| 13 | `cold/warming/ready` | `candidates.py`：`build_selection_audit()` 的 warmup 判定 | 已具备；cold/ready 人工样例和错误轮通过 |
| 14 | 同板块限制与稳定器排除原因 | `candidates.py`：`_selection_stage()`、`same_sector_limit`、`retained_by_stability`、`raw_top3_blocked_by_stability` 等 | 已具备；同板块限制、保留与排除原因可解释 |
| 15 | 人工结果“正式确认/即时预览” | `tushare_v1_session.py`：`_manual_result_ready()`、`_publish_manual_result()` | 已具备；cold 即时预览、ready 正式确认样例通过 |
| 16 | 人工结果不覆盖固定弹窗 | `tushare_v1_session.py`：固定提醒写入 `pending_alert` 后人工发布只在为空时写入 | 已具备；专项离线演练通过 |
| 17 | 最近 30 天未提醒 observation 不被误删 | `src/stock_watcher/storage/sqlite.py`：`prune_history()` 以 snapshot 时间边界清理 | 已具备；回归测试和迁移保留数据通过 |
| 18 | runtime universe seed | `scripts/export_runtime_universe_seed.py`；`universe_cache.py`：`install_seed()`；`paths.py`：`packaged_universe_seed_path()` | 已具备；5534 profiles、5532 memberships、5530 trends；无 secret hit；冷启动不覆盖既有缓存 |
| 19 | `SOURCE_COMMIT` | `src/stock_watcher/build_info.py`；`packaging/stockwatcher-macos.spec`；`ui/main_window.py` 开发信息页 | 已具备；arm64 ad-hoc App 嵌入当前 HEAD；Replay 开发信息页显示匹配 commit；真实 Tushare 页按设计隐藏开发菜单 |
| 20 | audit 导出脚本 | `scripts/export_selection_audit.py`：`scan-runs.json` 与 `candidate-audit.csv` | 已具备；SQLite 样例导出通过 |

## 证据索引

- 工程门、迁移、离线演练、冷启动和 Top3 审计：`/Users/kahlilhazel/Documents/700-AI-Workspace/90-Archive/StockWatcher/automation-repair-preapply-20260803-2203/engineering-gate/`；最终工程门见归档中的 `engineering-gate-final/`。
- App 构建归档见 `app-build-final/dist/StockWatcher.app`；arm64、ad-hoc codesign PASS；`SOURCE_COMMIT`、seed 比对和可执行文件 SHA-256 见 `engineering-gate-final/app-build-final.txt`。
- 真实 App 只读 UI：Token 状态显示“系统钥匙串已就绪”，未输出 Token；隔离 Replay App 的“开发信息”页显示相同 `SOURCE_COMMIT`。
- 当前真实缓存的概念状态是 `concept_loaded=false`；这不是概念 Live 通过证据，下一交易日需单独验证。
- 下一交易日 Mac Live 验收仍未完成；Mac 证据不得外推 Windows。
