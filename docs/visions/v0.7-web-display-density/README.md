# v0.7 Web 显示大小与紧凑看盘

> 状态：隔离预览已验证；未部署现网；Web 继续 `BLOCKED / NOT_ACCEPTED`。
> 日期：2026-09-10
> 工作树：`90-Archive/StockWatcher/00-current/app-mac-web-sync`
> 分支：`feat/web-display-density`（基于 `codex/ui-motion-performance` / `0.7.0-alpha.3`）

## 目标

在现有浅色 Web 界面上增加显示大小调节，并提供「仅看三只」紧凑观察布局。
不改行情、排名、提醒或数据库；不中断现网容器。

## 设计

- **入口**：登录后右上角账户旁，文案为「显示大小」加当前百分比。不是单独图标。
- **调节**：点击打开面板，拖动滑杆或按 − / +，范围 20%–150%，步进 5%。缩小后内容仍铺满窗口，避免竖屏两侧大块空白。立即生效，无需应用。
- **恢复**：面板内「恢复 100%」；紧凑模式下顶栏常驻「退出仅看三只」。顶栏本身不随内容缩小。
- **仅看三只**：独立于显示大小。只收起当前观察页的介绍、复盘摘要和次要按钮，保留三只候选、数据状态、候选时间和页面连接文案。
- **偏好**：`localStorage` 键 `stockwatcher.ui.scale`（全站）与 `stockwatcher.ui.watchLayout`（仅当前观察页生效）。保存失败时仍可当场调节。

## 主要文件

- `src/stock_watcher/server/templates/base.html`
- `src/stock_watcher/server/static/display.js`
- `src/stock_watcher/server/static/display.css`
- `tests/test_ui_display.mjs`
- `evidence/web-display-density/`

## 验证

浏览器：Playwright Chromium。数据：模拟预览页，不是真实行情。

| 场景 | 结果 |
| --- | --- |
| 1280×820 默认 100% | 入口在账户旁，文案清楚 |
| 打开面板并拖到 80% / 125% / 20%–150% | 立即缩放；缩小后仍铺满窗口；无横向溢出 |
| 竖屏 430×860 + 75% | 左右铺满，不再留大块空白 |
| 仅看三只 + 竖屏 | 三张候选收成可扫读的横条，顶栏可退出 |
| 陈旧快照 + 连接在线 | 「陈旧 / 保留快照 / 候选时间」与「实时连接在线」分开 |
| 预热、候选不足 | 显示等待卡片，不伪造行情 |
| 历史页带着紧凑偏好 | 历史内容完整，显示大小 80% 仍在 |
| 390 宽 | 入口仍在，无横向溢出 |
| 键盘 | 滑杆可调，Escape 关闭面板 |

命令：`node --test tests/test_ui_display.mjs`（8 passed）；`uv run python evidence/web-display-density/capture.py`。

截图（均标注模拟数据）：`evidence/web-display-density/*.png`。

## 未验证

- 未打开现网 `stock.hazelkahlil.com`，也没有重启 Web/Worker。
- 未用真实交易日行情、真实登录会话或 Safari / Firefox 手工验收。
- 未做 Mac / Windows 原生客户端改动，也未授权发布。
