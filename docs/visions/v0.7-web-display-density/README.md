# v0.7 Web 显示大小与紧凑看盘

> 状态：PR #12 返修已推送，等待复审；未合并、未部署。
> 工作树：`90-Archive/StockWatcher/00-current/app-mac-web-sync`
> 被测代码：`014e53ef084bcea81af9922636e8a1e43c33ce31`
> 证据提交：见本文件所在分支最新 docs/evidence 提交

## 本轮返修（相对已审核 `967a339`）

不再把“调小”主要做成把 `#main` 压到 `16rem` 细柱。显示比例只驱动密度变量（名称/代码/报价字号、卡片内边距、间距、排名尺寸、水平页边距）。列宽跟随窗口，历史/复盘页不再被挤成细条。

卡片主行只放身份和报价；级别、板块、状态进次信息组。窄容器才把次组换到第二行。四字名称 `word-break: keep-all`，代码 `nowrap` 且覆盖旧的 `0.86rem !important` 与 ellipsis。

## 本轮测量（Chromium，模拟数据 `preview-fixtures-v2`）

`uv run python evidence/web-display-density/capture.py`

| 场景 | 视口 | 主列宽 | 卡高 | 名称高 | 代码裁切 |
| --- | --- | --- | --- | --- | --- |
| 100% 完整 | 1280×820 | 1265 | 94 | 28 | 否 |
| 35% 完整/紧凑 | 1280×820 | 1265 | 64 | 21 | 否 |
| 20% 紧凑 | 1280×820 | 1265 | 61 | 18 | 否 |
| 100% 窄窗 | 430×860 | 415 | 126 | 28 | 否 |
| 320 100% | 320×640 | 305 | 147 | 28 | 否 |
| 320 20% 紧凑 | 320×640 | 305 | 93 | 18 | 否 |

20% 与 35% 主列宽相同（都铺满窗口），名称高度 18 vs 21，卡片 61 vs 64。旧 `after-metrics.json` 里 20%/35% 都是 360×328 的记录作废，那是上一轮压列宽实现的产物。

截图：`evidence/web-display-density/round2-*.png`，与 `round2-metrics.json` 同一次 capture 生成。

## 复现

```bash
uv run python evidence/web-display-density/capture.py
node --test tests/test_ui_display.mjs
uv run pytest tests/test_web_display_layout.py
uv run ruff check .
uv run mypy src tests
uv run python scripts/validate_workspace.py
```

预览：`python3 -m http.server 8765 --bind 127.0.0.1`
`http://127.0.0.1:8765/evidence/web-display-density/preview-dashboard.html`

## 未验证

Safari、Firefox 手工；真实行情登录；现网。未合并、未部署。
