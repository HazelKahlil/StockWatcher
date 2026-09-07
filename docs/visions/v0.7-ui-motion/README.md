# v0.7 Web / Mac 流畅交互

2026-09-06 - UI delivered - Owner: Codex

目标：继续优化 Web 与 Mac App 的交互、过渡、视觉便利性，保留现有苹果式浅色设计语言。
基于已安装 alpha.2 产品 f3a31a3 / 记录 c95a03d，候选版本 0.7.0-alpha.3。
沿用本机隔离验证 → 同源配对构建 → 更新现有安装的授权；不合 main，不 push。

## 完成要求

- Web 切页、详情开关、折叠、筛选和加载有连续反馈；动效短且可打断，失败或快速操作不造成状态错乱。
- Web 数据刷新复用候选节点，保留焦点；长列表按需展开，保留展开位置和原排名。
- Mac 数据更新复用控件，历史/复盘避免一次创建全部记录；读取和导出不阻塞主界面。
- Mac 手动面板使用系统异步 sheet，关闭立即响应；在异步加载中关闭安全，结果不再弹回。
- 两端尊重减少动态效果；不增加自动提醒的焦点打扰，不改变排名、行情、胜率分母或持久化规则。
- 实际浏览器与打包 Mac 测试涵盖连续开关、快速筛选、重复数据刷新、大量记录、失败恢复、窄屏与键盘。
- 记录实际 UI 性能证据和限制，完成 pytest / Ruff / Mypy / Node / workspace / 回放 / diff 验证。
- 同一提交构建并安装 Mac / Web，读回版本、登录、数据库水位与原有页面功能。

## 已定位问题

- Mac 每次刷新重新创建三张候选卡；复盘一次创建全部记录；历史打开同时加载隐藏复盘。
- Mac 历史关闭可同步等待两个线程各两秒；盘后总结读取与 PDF 准备位于 UI 线程。
- Web 每次候选数值改变替换整组节点；详情直接开关，details 突变；折叠后的复盘记录仍全部构建。

## 参考与边界

Apple HIG Motion、Understanding UI Responsiveness；WebKit cross-document View Transitions。
视觉用短淡入/位移建立连续性；减少动态效果时直接更新。不让动画推迟新行情和状态显示。
Windows 和真实交易日业务验收保持独立；现网数据库恢复、21 笔历史 pending 不在本次范围。

## 实现与本地验证（2026-09-06）

已实现候选节点/控件复用、Web 短过渡及减少动态效果、按日期延迟创建明细、
Mac 原生异步面板、18 条一批的历史/复盘、后台报告读取与 PDF 准备/保存。
历史关闭不再 wait；后台线程独立持有生命周期，销毁视图后自动断开 Qt 接收端。

- 全量 pytest 通过；新增千条历史分批可达、读取未结束关闭及候选控件身份测试。
- Ruff / Mypy（148 源文件）、Node（3 项）、workspace（29 文件）、Windows 离线包契约通过。
- CUA 隔离 Web：详情开关/Esc/焦点返回、日期展开、筛选保留展开、异步总结正文；390px 无页面横向溢出，控制台无 error/warn。
- Mac Qt offscreen 100 次同状态全窗口刷新：alpha.2 中位 0.563ms / P95 1.113ms；
  alpha.3 中位 0.114ms / P95 0.428ms。仅受控渲染开销，不是现场帧率保证。
- 原始证据：`90-Archive/StockWatcher/99-deliveries/StockWatcher-Motion-20260906/`。
- 打包 Mac 实际交互、配对安装与生产读回仍在进行；不得将本节当作发布或业务验收完成。

打包 UI 检查补充：Mac 720px 高度下统计挤占明细区域，进一步将四项统计并排、
日组合文字明细默认收起（可展开）。b4b3c62 为中间候选，最终配对提交以后续记录为准。

## Final delivery - 2026-09-06

UI implementation and paired installation complete; live-market and Windows acceptance remain separate.

- Product source: `5f64d0a9ea266196accfc0b9fea1c9fd973bb188`; Mac and Web `0.7.0-alpha.3`.
- Mac: `/Users/kahlilhazel/Applications/StockWatcher.app`; signature, embedded commit and executable hash match the release manifest.
- Web/Worker: `stockwatcher-web:mac-web-0.7.0-alpha.3-5f64d0a`; both healthy, restart count 0. Gateway/cloudflared unchanged.
- Public homepage/ready 200; existing Chrome login retained; details/Esc/review verified.
- Original Top3 codes: 300829.SZ, 300741.SZ, 300106.SZ. Review statistics remain 75/96, 29.3%, 27.3%, -1.53%.
- Before/after: snapshot 7862, source 2026-09-04T15:00:05+08:00; snapshots 7777, items 23323, outcomes 96, summaries 17, users 6.
- Qualified backup: `/backups/ui-motion-final-20260906T045620Z/stockwatcher.db`; offline integrity ok, FK 0, copied hash verified; reports retained alongside.
- Full pytest: 591 passed / 25 skipped; final layout changes: 68 relevant tests passed. Ruff/Mypy/Node/workspace/offline Windows package contract passed.
- Native UI: repeated history sheets, lazy review, range change, Esc, portfolio disclosure and actual PDF save verified. Final screenshot: `mac-outcomes-final.png`.
- Evidence: `/Users/kahlilhazel/Documents/700-AI-Workspace/90-Archive/StockWatcher/99-deliveries/StockWatcher-Motion-20260906`; `release.json`, `ui-verification.json`, before/after DB validation, refresh benchmark, screenshots and fixture PDF.
- Original alpha.2 App: `mac-before-alpha2.app`; intermediate candidates are superseded by the product source above.
- Preview and Replay processes stopped. Local commits only; no main merge or push; GitHub not synchronized.

Limits: timings measure Qt offscreen unchanged refresh work, not whole-system FPS. Reduced-motion support implemented; user OS settings were not changed for an end-to-end comparison. No market-provider calls. Existing 21 pending settlements, recovery strategy and real trading-day acceptance remain separate work.


## 2026-09-07 Mac crash investigation and runtime recovery

At 14:41, production Web/local readiness were 200, all StockWatcher containers healthy, Web snapshot source 14:41:42 advancing. No Mac StockWatcher process existed. The Mac last-startup record identified PID 76548 / source 5f64d0a / event-loop-entered with no normal exit record. Preserved faulthandler output contains native segmentation faults; its latest write time was 10:33. The final stack shows a native worker without Python frame and the main thread in app.exec(), which is insufficient to identify the crashing native call. Do not label this as a network disconnect or claim a proven shared cause with Web SQLite corruption.

Before reopening the existing installed App, saved startup/crash evidence and a SQLite backup under `99-deliveries/StockWatcher-Live-20260907/mac-crash-1442` in the StockWatcher archive. Offline backup integrity OK, FK empty. Reopened the existing 0.7.0-alpha.3 build through the native app interface; UI became HEALTHY with ranked Top3 and database snapshot 99 / source 14:43:48. This restores operation only; native crash root cause remains open, owner Codex. No executable replacement, Windows process operation, or Web restart was performed.

User-supplied Windows report: installed 50904b6 / alpha.6, PID 5912, window unresponsive, heartbeat stopped 09:08, no current-session scan attempts. This establishes an unresponsive UI but does not exclude a blocking provider/database call before attempt persistence. Native hang stacks remain needed; do not attribute it to the uninstalled PR #9 candidate or count downloaded CI artifacts as installed acceptance.
