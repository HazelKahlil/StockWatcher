# v0.7 UI 细节与交互优化

2026-09-06 · implemented_verified_installed · Owner: Codex

用户要求保留现有苹果式设计语言，优化整体 UI、细节和交互。源码基于同源 Mac/Web
`8f34c32` 及收尾文档 `b4defcf`，使用 `codex/ui-detail-refinement`；候选版本
`0.7.0-alpha.2`。不合 main，不 push。已验证后同步安装 alpha.2；alpha.1 安装与数据库备份均保留。

## 范围

- 保留系统字体、浅底白卡、蓝色操作、A 股红涨绿跌及紫色重复标记。
- 全站导航、字号、间距、表单、加载/错误/空态、窄屏适配统一。
- 首页区分候选源时间、扫描完成时间和页面连接；候选详情支持即时反馈、键盘、关闭及竞态保护。
- 复盘明细按日分组、筛选；回补提示与当前结算统计分开，保持原分母与六笔完整日组合规则。
- 历史支持 Enter 筛选、重置与可靠追加；总结支持正文阅读和下载失败反馈；提醒保持低打扰。
- Mac 配套提升键盘可达性、焦点和回补状态表达；两端保持相同源码版本。

## 现场问题

Chrome 真实页面确认：导航没有选中标记；详情请求前无加载反馈，Esc 不关闭；原始 JSON
直接占据详情；96 笔复盘记录全部展开；历史/总结缺少完整重试反馈。
首页把扫描时间当候选更新时间、回补 skipped 被表述成统计排除数量，均需澄清。

## 验证与边界

使用隔离 Replay / 合成 Web 数据进行真实 Chrome 与 Mac UI 检查；不触发盘中扫描、通知权限、
生产账号变更或交易。产品改动执行 pytest、Ruff、Mypy、回放 smoke、workspace 和 diff 检查。
Web 既有恢复/报告目录问题、21 笔待结算与 Windows 真机验收仍独立保留。

参考 Apple [Human Interface Guidelines](https://developer.apple.com/design/human-interface-guidelines)：
让控件与内容形成清晰层级；本次保留现有界面语汇，不引入外部字体、图标服务或装饰素材。

## 初步验证（2026-09-06）

- macOS 全量 pytest：588 passed / 25 skipped / 2 deselected；Ruff、Mypy 147 文件通过。
- Node 3 项展示逻辑回归：扫描时间不能刷新旧候选、上海时区周末、筛选不重排原始 TOP rank。
- Chrome 原生 UI：登录合成预览、详情 Esc 关闭后焦点回到原候选，Enter 可再次打开。
- 独立浏览器：复盘按状态/股票筛选，原 TOP 2 与 30 笔统计保持；总结正文与 PDF 缺失提示；
  历史 Enter 空态、重置恢复 4 轮、加载更多到末尾。小屏详情与主卡片无横向溢出。
- 以上均为 Replay/合成数据，未调用行情供应商；Mac 打包候选与版本安装读回待补。

## 最终验证与安装

产品提交 `f3a31a35f717e528e3d68e9bac1205f58d59243e`，Mac / Web 均为
`0.7.0-alpha.2`。Mac 安装在 `~/Applications/StockWatcher.app`；Web / Worker 使用
`stockwatcher-web:mac-web-0.7.0-alpha.2-f3a31a3`，镜像 ID
`sha256:39cc00b66c021c037ca3a52bdeff3a0ef61b87e06b3ce7ba597cd1cccba897b8`。
两端版本、内嵌 source commit 与配对构建清单相符；Mac 为本机 ad-hoc 签名，未启动真实数据会话。

- 最终全量 pytest：589 passed / 25 skipped / 2 deselected；Ruff、Mypy 147 文件、Node 3 项、
  全部 Web JS 语法、workspace 29 文件、Windows 离线打包契约与 diff 检查通过。
- Mac 最终打包 Replay 实际执行 `Tab → Space → Esc → Enter`，可返回原候选并再次打开；
  详情改为异步 `open()`，避免原生 Mac 中嵌套 `exec()` 返回后主窗仍被阻塞。
  测试窗口在用例结束时清理 DeferredDelete，防止影响随后单实例事件循环测试。
- Web 实际 CSS 宽度 390px 下主页/复盘无页面横向溢出，导航可横向滚动，详情自适应整屏。
- Chrome 已登录现网：页脚 alpha.2；源时间 `2026-09-04 15:00:05` 与扫描时间分开，显示
  “周末休市”“保留快照”，三只原排名可读。复盘仍为 75/96，个股 29.3%、日组合 27.3%、
  平均 -1.53%，11 个完整组合日；17 份总结可读，09-04 正文展开与 PDF 7.8 KB 下载完成。
- 公网首页、`/health/ready`、本机 ready 均 200；`/health/version` 返回 alpha.2 / f3a31a3。
  Web/Worker healthy、restart 0。Cloudflared 与 gateway 保持原实例。

证据目录（仓库外）：
`90-Archive/StockWatcher/99-deliveries/StockWatcher-UI-20260906/`。
其中 `ui-verification.json`、`web-before-validation.json`、`web-after-validation.json`、
`stockwatcher-ui-pytest-complete.log` 与 `f3a31a35f717/release.json` 为最终记录；
`production-home-after.png`、`web-outcomes-desktop.png`、`web-home-narrow.png`、
`mac-focus-verified.png`、`mac-detail-verified.png` 为界面证据。
先前 `7d150a1590b7` / `24534006107b` 构建被最终包取代，未安装或部署。

## 数据与回滚

切换前使用 SQLite backup 制作一致性副本，仅对离线副本执行全量完整性检查，结果 ok、FK 0。
最新恢复候选 `/backups/ui-alpha2-20260906T035535Z/stockwatcher.db`，SHA-256
`878be600567a4cea1e3c166e8000c71998dfc90799ed5529f5c7e316bcbce6f9`；同目录 `reports/`
保存 16 个哈希一致的报告文件。切换前后 Schema 10、max snap 7862、快照 7777、候选项 23323、
复盘 96、总结 17、用户 6 均一致，没有 Schema 迁移或业务记录回退。

旧 Mac App 保留在证据目录 `mac-replaced.app`（另有切换前复制的 `mac-before.app`）。
需要回滚 UI 时使用旧 alpha.1 App / 镜像 `stockwatcher-web:mac-web-0.7.0-alpha.1-8f34c32`，
配对 SOURCE_COMMIT `8f34c32580460be4c7e81c2d58190c0726657ab1`；Web 必须沿用原三个 compose
文件及 bind-db。UI 回滚不需要恢复数据库。部署配置记录在 ops 提交 `0d8f3ec`。

## Review 与独立欠账

P0/P1：本次最终差异未发现未处理问题；展示层过滤不改变排名/统计，接口只共用安全文案，
手动详情与右下角自动提醒分离，自动提醒的无焦点打扰、Esc 清队列等既有测试通过。
真实交易日仍为 Web `BLOCKED / NOT_ACCEPTED`，Windows 未更新。
历史 21 笔 pending、恢复候选业务日期选择、报告目录统一及周末运行时日历回退问题仍由
Codex 后续独立处理；本次周末标签仅修正显示，没有改变行情日历与供应商调用规则。

源码工作区未合 main、未 push。Ops 中原有 `deploy/docker-compose.yml` 与
`src/stock_watcher/server/healthcheck.py` 两项未提交修改继续保留，本次没有把它们混入提交。
