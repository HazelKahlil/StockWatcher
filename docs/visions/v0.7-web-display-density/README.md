# v0.7 Web 显示大小与紧凑看盘

> 状态：PR #12 定向返修已推送，等待复审；未合并、未部署。
> 被测实现：`029a90d3e2f7dc9846184c559d77e7fb9adfa255`
> 证据提交：见其后仅含截图/测量/说明的提交

## 本轮相对 `3680405`

保留页面宽度与显示密度分离。候选卡改用 **subgrid**：身份列为 `max-content`，报价列为内容宽度，剩余给板块等辅助信息。宽屏 35%/20% 名称到报价的空隙从约 560px 收到约 8–17px。报价不再使用固定 `5.4rem`。

几何检查改为三张卡全部测量，用 `Range` 取文本矩形，并做名称/代码与报价的碰撞检查。长名称与等待卡不再跳过溢出和重叠。

新增正式 `dashboard.html` + `dashboard.js` 模拟接口测试：详情打开/关闭焦点返回、刷新 POST、拖动滑杆不发 manual-refresh。

Linux CI 新增 `Web display layout` 作业，安装 Chromium，`STOCKWATCHER_REQUIRE_UI=1` 时缺浏览器失败。pytest 把测量写到临时目录；只有 `capture.py` 写入 `evidence/web-display-density/`。

## 测量（Chromium，`harness-fixtures-v3`）

`uv run python evidence/web-display-density/capture.py`

| 场景 | 主列宽 | 卡高 | 名称→报价空隙 | 重叠 |
| --- | --- | --- | --- | --- |
| 1280 100% | 1280 | 94 | 14–20px | 否 |
| 1280 35% | 1280 | 64 | 8–12px | 否 |
| 1280 20% 紧凑 | 1280 | 61 | 14–17px | 否 |
| 430 100% | 430 | 126–147 | 两行布局 | 否 |
| 390 150% 长名称 +129.99% | 390 | 197 | 无重叠 | 否 |

截图：`round3-*.png`，与 `round3-metrics.json` 同一次生成。

## 复现

```bash
uv run python evidence/web-display-density/capture.py
node --test tests/test_ui_display.mjs
uv run pytest tests/test_web_display_layout.py
uv run ruff check .
uv run mypy src tests
uv run python scripts/validate_workspace.py
```

## CI 归属（Governance）

| 项目 | 处理 |
| --- | --- |
| Web Ruff/Mypy/工作区校验 | 实现阶段已通过 |
| secret-scan 证据行过期 | 基线证据，不在本 PR 改 |
| 工作区空白 | 旧审计 diff，与本轮 UI 文件无关 |
| Dependency review | 仓库未开 Dependency graph |
| Windows SQLite / CNB registry | 非本 PR 范围；未在相同 Windows 环境重跑 base |
| 本轮 UI 回归 | 新增 Ubuntu `web-display-ui` 作业强制执行 |

## 未验证

Safari、Firefox 手工；真实行情；现网。未合并、未部署。
