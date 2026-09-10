# v0.7 Web 显示大小与紧凑看盘

> 状态：布局修复已在隔离预览验证；未部署现网；Web 继续 `BLOCKED / NOT_ACCEPTED`。
> 日期：2026-09-10
> 工作树：`90-Archive/StockWatcher/00-current/app-mac-web-sync`
> 功能分支：`feat/web-display-density`
> 本轮修复：`fix/web-display-density-responsive-layout`

## 问题与根因（已测量）

Playwright Chromium。视口是 CSS `window.innerWidth/innerHeight`，不是截图像素宽。
预览页使用正式 `app.css` / `display.css` / `display.js`，卡片 HTML 来自正式 `candidate-card.js`（与 `dashboard.js` 同一模块）。

| 场景 | 视口 | 比例 | 修前卡片 | 已证实原因 |
| --- | --- | --- | --- | --- |
| 100% 窄窗 | 430×860 | 100 | 398×**172**，3 列网格，level y=74、sector y=119 | `refinements.css` `@media (max-width: 720px)` 按**视口**把卡拆成三行；`min-height: 110px` + `level-tag min-height: 4.3rem` 把短内容撑高 |
| 35% 宽屏 | 1280×820 | 35 | 1253×**39**，6 列 `fr`，报价在视觉最右侧 | `#main { width: 100svw / scale; zoom: scale }` 让子元素在约 3 倍宽的 CSS 画布上排版，`minmax(..., 1fr)` 把空白吸到身份列和板块列之间 |

未证实、本次未当根因处理：浏览器自身缩放、Safari 容器查询差异。

## 修改

- 去掉用 zoom 放大 CSS 宽度的做法。显示比例改为约束 `#main` 内容宽度，并用 `clamp` 缩放字号；20% 与 35% 的列宽不同，不是把下限改成 35%。
- 候选卡改为按**卡片容器宽度**组织：宽时 `身份 | 行情 | 板块 | 详情`；窄于 36rem 时两行 `身份+行情 / 板块+详情`。不再用视口 720px 一刀切成高卡片。
- 级别标签回到身份组里，取消 4.3rem 高块。
- 紧凑模式始终只保留数据状态、候选时间、页面连接文案；不再依赖视口 720px 才收起状态栏。
- 卡片 HTML 抽到 `candidate-card.js`，dashboard 与 preview 共用。

未改：候选数量、排名、行情源、扫描、提醒、复盘统计、数据库。

## 同条件前后（卡片高度）

| 场景 | 修前 | 修后 |
| --- | --- | --- |
| 100% × 430 窄窗 | 172px，三行，级别独占一行 | **118px**，两行，级别跟名称 |
| 100% × 1280 桌面 | min-height 7.1rem | **77px** 单行 |
| 35% × 1280 | 1253×39，小字贴两端 | 列宽约 358px，卡 **111px**，身份和涨跌成组 |

截图：`evidence/web-display-density/before-*.png` 与 `after-*.png`。

## 复现

```bash
cd 90-Archive/StockWatcher/00-current/app-mac-web-sync
python3 -m http.server 8765 --bind 127.0.0.1
# 打开
# http://127.0.0.1:8765/evidence/web-display-density/preview-dashboard.html
```

卡片由 `/src/stock_watcher/server/static/candidate-card.js` 生成，样式是正式 `display.css`。

`node --test tests/test_ui_display.mjs`

## 未验证

- 现网、真实登录、Safari / Firefox 手工、Mac/Windows 原生客户端。
- 未合并、未部署。
