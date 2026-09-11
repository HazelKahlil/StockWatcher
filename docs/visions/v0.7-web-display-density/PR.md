## 目标与版本

- Issue：Web 显示大小 / 仅看三只，窄窗与 35% 比例下列卡片过早拆行、内部空白过大
- 目标版本：`v0.7-web-display-density`
- 解决的问题：100% 窄屏候选卡被视口断点拆成高卡片；35% 时 zoom 放大 CSS 宽度，小字贴在宽卡片两端
- 明确不在本 PR 范围内：现网部署、行情/排名/提醒/复盘业务、Mac/Windows 原生客户端、合并到 `main`

## 改动

- 候选卡信息改按容器宽度组织（宽：身份｜行情｜板块｜详情；窄：两行），级别回到身份组
- 显示比例改为收缩内容列宽并 clamp 字号，不再用 `zoom + 100svw/scale` 把布局画布拉宽
- 紧凑模式始终保留数据状态、候选时间和页面连接文案，不把两者收成一个绿点
- 卡片 HTML 抽到 `candidate-card.js`，dashboard 与 preview 共用

## 验证证据

- 运行环境：macOS / Playwright Chromium / 模拟数据（非真实行情）
- [x] 自动测试：`node --test tests/test_ui_display.mjs`（9 passed）
- [x] Python UI：`STOCKWATCHER_REQUIRE_UI=1 uv run pytest tests/test_web_display_layout.py -W error`（几何、正式 dashboard 交互、harness 异常释放、注入 POST 负向断言）
- [x] 同视口前后测量：见 `evidence/web-display-density/round3-metrics.json`（实现 `029a90d`；round2 的 561/564 是 `quoteLeft`，不是 `nameToQuoteGap`）
- [ ] Windows / 通达信现场验证（如适用）
- [x] 未把 Mac/Replay 结果表述为 Windows/通达信已验证

卡片高度（同一视口、同一比例、同一模拟数据）：

- 100% × CSS 430×860：172px → 118px
- 100% × CSS 1280×820：min-height 7.1rem → 77px
- 35% × CSS 1280×820：1253×39 的两端小字 → 约 358px 列宽、卡 111px

## 风险核对

- [x] 未读取交易账户或调用下单接口
- [x] 数据中断时不会产生伪候选
- [x] 未静默改变 `requirements.lock.json`
- [x] 未提交密钥、用户配置、数据库、日志或行情缓存
- [x] 无 Schema / 供应商字段变化

## 文档与收尾

- [x] 已更新 `docs/visions/v0.7-web-display-density/README.md`
- [x] 未完成项：现网试用需另授权；Safari/Firefox 未手工测
