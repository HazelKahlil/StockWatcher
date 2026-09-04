# Windows PR #7 本机收口（2026-09-04）

> 结论：**PASS_WITH_LIMITS**。下周交易日再测 09:45。  
> **不得合并 PR #7，不得表述为“Windows 已验收”。**  
> 本文件只记录本机 Windows 事实，不能用 Mac / CI / 离屏结果覆盖。

## 人话

桌面关窗、Ctrl+Q、单实例已经在 9 月 1 日晚上复验通过。当天 14:45 账本和 15:30 PDF 也过了。**09:45 没过**，根因是上午旧包把预热卡死。修好的包当晚才装上，不能回放当天 09:45。

9 月 2–4 日应用没有再启动：库停在 9 月 1 日 20:41，没有新的扫描或定时任务。本机计划任务因电池策略 / 未登录没有真正拉起进程。周末不补做盘中项。

## 代码与安装

| 项 | 值 |
|---|---|
| 代码 PR | https://github.com/HazelKahlil/StockWatcher/pull/7 |
| 分支 | `fix/windows-desktop-stability` |
| 已推送 HEAD | `b2a4f157f27a7e45d71126f7bd81e60c65d1500e` |
| 本机实装 commit | `50904b6efed95a5544445082477aedbeb71ea906`（docs 提交 `b2a4f15` 不必重装） |
| 版本 | `0.6.0-alpha.6` |
| setup SHA-256 | `F5421681114A4C8500EC92671BFBFCEA4F0C27FBF482FD0315DBFE2FFC8E438D` |
| 机器 | Windows 11 10.0.26200，一块 1280×800 逻辑屏 @ 200% |
| Token | 只在 Credential Manager，未回显 |

本轮现场修复（已在 PR #7）：

- `c6240c9` 关闭时不再在持锁路径上 `Future.cancel`（过夜 GUI 死锁）
- `c5c467e` 停滞检测把扫完的 WARMING/STOPPED 也算活动，避免预热卡在 1/3
- `50904b6` `CreateFileW` share=0 互斥；二次进程在导入 Qt 前退出；显式绑定 Ctrl+Q

## 9 月 1 日实机结果

| 项 | 结果 |
|---|---|
| WM_CLOSE / 点 X | PASS，约 2189ms，`exit_code=0` |
| Ctrl+Q | PASS，约 436ms |
| 二次启动 | PASS，8s 后仍 1 个进程，无 46MB 残留 |
| 设置 → 数据接口 | PASS，能开能关；未输入 Token |
| 盘中扫描（11:10 换停滞修复包后） | PASS，下午 HEALTHY |
| `scheduled-09:45` | **FAIL**。09:30–09:47 STOPPED，无成功 alert |
| `scheduled-14:45` | PASS（账本 14:45:01 succeeded，`snapshot_id=31`）。截屏黑，弹窗未抓住 |
| `summary-15:30` | PASS，PDF 146304 bytes |
| 双屏 | 测不了 |

本机证据目录（不入库）：`C:\Users\19580\Desktop\StockWatcher-pr7-evidence-20260901`

## 9 月 2–4 日

无扫描、无 09:45/14:45、无盘后 PDF。`last-startup.json` 与 SQLite 停在 2026-09-01 20:41。计划任务 `StockWatcher-PR7-Preopen` 从未真正跑过（电池模式会停）。

## 下周只做这些

1. 确认已装 `SOURCE_COMMIT=50904b6…`，插电、用户已登录。
2. 交易日 **08:50 前** 启动 `%LOCALAPPDATA%\Programs\StockWatcher\StockWatcher.exe`。
3. 09:20–09:50 盯 HEALTHY 预热；09:45 必须同时有账本 `succeeded` 和可见弹窗截屏。
4. 14:45、15:30 复验；黑屏截屏不算视觉证据。
5. 盘中 09:25–15:35 不重装、不乱杀进程。
6. 仍不合并 PR #7，仍不写“Windows 已验收”。

## 交接提示词

下一会话把下面整段原样交给 Agent。

```
你在 Windows 本机继续 StockWatcher PR #7 验收，不要重新发明范围。

仓库：https://github.com/HazelKahlil/StockWatcher
代码 PR：https://github.com/HazelKahlil/StockWatcher/pull/7
分支：fix/windows-desktop-stability
已推送 HEAD：b2a4f157f27a7e45d71126f7bd81e60c65d1500e
本机应装 SOURCE_COMMIT=50904b6efed95a5544445082477aedbeb71ea906
版本：0.6.0-alpha.6
工作区：C:\Users\19580\Desktop\StockWatcher-pr7-acceptance-20260828
证据：C:\Users\19580\Desktop\StockWatcher-pr7-evidence-20260901
收口记录：docs/visions/v0.4-v1-feature-complete/windows-pr7-handoff-20260904.md

硬规则：
- 不合并 PR #7，不写「Windows 已验收」，不用 Mac/CI/离屏结果冒充本机。
- Token 只在 Windows Credential Manager，不回显、不写入配置/日志/截图/命令行。
- 只做观察提醒，不接交易账户、不下单。
- 盘中 09:25–15:35 不重装、不乱杀进程。进程死了才能拉起已装 exe。

已完成（2026-09-01 本机，不要重测除非回归）：
- WM_CLOSE / Ctrl+Q / 单实例无 46MB 残留 / 设置页开闭：PASS
- scheduled-14:45 账本 PASS（弹窗截屏黑，视觉未证明）
- summary-15:30 PDF PASS（146304 bytes）
- 停滞自救、互斥锁、Ctrl+Q 已进 PR #7

未完成：
- scheduled-09:45 在 2026-09-01 FAIL；2026-09-02..04 应用没跑，没有新证据
- 双屏本机没有，标测不了
- 14:45 弹窗视觉仍要补截屏

本周任务（下一个交易日，通常周一 09:45）：
1. 插电、用户已登录。确认 %LOCALAPPDATA%\Programs\StockWatcher\_internal\stock_watcher\SOURCE_COMMIT 是 50904b6。
2. 08:50 前启动 StockWatcher.exe，窗口 Responding。
3. 09:20–09:50 看扫描是否 HEALTHY；09:45 必须同时有 sqlite automation_tasks 的 succeeded 和可见弹窗截屏。
4. 14:45、15:30 同样要账本 + 非黑屏截屏。
5. 过了就更新收口记录并回评论 PR #7；仍 FAIL 就修产品再等下一个交易日。不要为了收口而合并。
```
